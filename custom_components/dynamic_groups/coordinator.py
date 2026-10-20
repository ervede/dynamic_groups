"""Coordinator that resolves labels/areas into entity lists."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import slugify

from .const import (
    CONF_AREA,
    CONF_DOMAIN_FILTER,
    CONF_LABEL,
    CONF_NAME,
    DOMAIN,
    GROUP_TYPE_AREA_DOMAIN,
    GROUP_TYPE_AREA_LABEL,
    GROUP_TYPE_LABEL_DOMAIN,
    GROUP_TYPES,
    OBJECT_ID_PREFIX,
    REGISTRY_REFRESH_COOLDOWN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\w")


def is_valid_group_name(name: str) -> bool:
    """Return whether a name yields a usable object id.

    Home Assistant's slugify() maps names without any letters or digits to
    "unknown", which would make unrelated groups collide, so reject them.
    """
    return bool(_WORD_RE.search(name)) and bool(slugify(name))


@dataclass(frozen=True, slots=True)
class GroupDefinition:
    """A user-configured dynamic group (one config subentry)."""

    group_id: str  # the subentry id
    group_type: str
    name: str
    area_id: str | None
    label_id: str | None
    domain: str  # empty string means "any domain" (area + label groups only)

    @property
    def object_id(self) -> str:
        """Return the object id suggested for the group entity."""
        return f"{OBJECT_ID_PREFIX}{slugify(self.name)}"


@dataclass(frozen=True, slots=True)
class ResolvedGroup:
    """The result of resolving one group definition."""

    definition: GroupDefinition
    entity_ids: tuple[str, ...]
    missing: tuple[str, ...]  # descriptions of labels/areas that do not exist


def parse_group_definitions(entry: ConfigEntry) -> list[GroupDefinition]:
    """Build group definitions from the entry's subentries, skipping bad ones."""
    definitions: list[GroupDefinition] = []
    seen_object_ids: set[str] = set()

    for subentry in entry.subentries.values():
        if subentry.subentry_type not in GROUP_TYPES:
            continue
        data = subentry.data
        name = str(data.get(CONF_NAME, "")).strip()
        area_id = data.get(CONF_AREA)
        label_id = data.get(CONF_LABEL)
        domain = str(data.get(CONF_DOMAIN_FILTER) or "").strip().lower()

        needs_area = subentry.subentry_type in (
            GROUP_TYPE_AREA_DOMAIN,
            GROUP_TYPE_AREA_LABEL,
        )
        needs_label = subentry.subentry_type in (
            GROUP_TYPE_LABEL_DOMAIN,
            GROUP_TYPE_AREA_LABEL,
        )
        needs_domain = subentry.subentry_type != GROUP_TYPE_AREA_LABEL

        if (
            not is_valid_group_name(name)
            or (needs_area and not area_id)
            or (needs_label and not label_id)
            or (needs_domain and not domain)
        ):
            _LOGGER.warning(
                "Ignoring malformed dynamic group '%s' (%s)",
                subentry.title,
                subentry.subentry_id,
            )
            continue

        definition = GroupDefinition(
            group_id=subentry.subentry_id,
            group_type=subentry.subentry_type,
            name=name,
            area_id=str(area_id) if needs_area else None,
            label_id=str(label_id) if needs_label else None,
            domain=domain,
        )

        if definition.object_id in seen_object_ids:
            _LOGGER.warning(
                "Ignoring group '%s': another group already uses the name",
                definition.name,
            )
            continue

        seen_object_ids.add(definition.object_id)
        definitions.append(definition)

    return definitions


class DynamicGroupsCoordinator(DataUpdateCoordinator[dict[str, ResolvedGroup]]):
    """Resolve every configured group to the entities it should contain."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REGISTRY_REFRESH_COOLDOWN,
                immediate=False,
            ),
        )
        self._warned_missing: dict[str, tuple[str, ...]] = {}
        self._last_counts: dict[str, int] = {}

    @callback
    def async_start_registry_listeners(self) -> None:
        """Refresh quickly when a registry that affects membership changes."""
        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
            lr.EVENT_LABEL_REGISTRY_UPDATED,
        ):
            self.config_entry.async_on_unload(
                self.hass.bus.async_listen(event_type, self._handle_registry_event)
            )

    @callback
    def _handle_registry_event(self, _event: Event[Any]) -> None:
        """Schedule a debounced refresh after a registry change."""
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_request_refresh(),
            name=f"{DOMAIN} registry refresh",
        )

    async def _async_update_data(self) -> dict[str, ResolvedGroup]:
        """Resolve all groups."""
        try:
            definitions = parse_group_definitions(self.config_entry)
            return {
                definition.group_id: self._resolve(definition)
                for definition in definitions
            }
        except Exception as err:  # noqa: BLE001 - never let a bad scan crash HA
            raise UpdateFailed(f"Failed to resolve dynamic groups: {err}") from err

    def _resolve(self, definition: GroupDefinition) -> ResolvedGroup:
        """Resolve a single group definition."""
        missing: list[str] = []
        candidates: set[str]

        if definition.group_type == GROUP_TYPE_LABEL_DOMAIN:
            assert definition.label_id is not None
            candidates, label_missing = self._entities_for_label(definition.label_id)
            if label_missing:
                missing.append(f"label '{definition.label_id}'")
        elif definition.group_type == GROUP_TYPE_AREA_DOMAIN:
            assert definition.area_id is not None
            candidates, area_missing = self._entities_for_area(definition.area_id)
            if area_missing:
                missing.append(f"area '{definition.area_id}'")
        else:
            assert definition.area_id is not None
            assert definition.label_id is not None
            candidates, area_missing, label_missing = self._entities_for_area_label(
                definition.area_id, definition.label_id
            )
            if area_missing:
                missing.append(f"area '{definition.area_id}'")
            if label_missing:
                missing.append(f"label '{definition.label_id}'")

        warn_key = definition.group_id
        if missing:
            if self._warned_missing.get(warn_key) != tuple(missing):
                self._warned_missing[warn_key] = tuple(missing)
                _LOGGER.warning(
                    "Group '%s': %s does not exist; the group will be empty",
                    definition.name,
                    " and ".join(missing),
                )
        else:
            self._warned_missing.pop(warn_key, None)

        if definition.domain:
            prefix = f"{definition.domain}."
            candidates = {e for e in candidates if e.startswith(prefix)}

        entity_ids = tuple(sorted(candidates))

        if self._last_counts.get(definition.group_id) != len(entity_ids):
            _LOGGER.debug(
                "Group '%s' (%s, domain '%s') resolved to %d entities",
                definition.name,
                definition.group_type,
                definition.domain or "any",
                len(entity_ids),
            )
            self._last_counts[definition.group_id] = len(entity_ids)

        return ResolvedGroup(
            definition=definition,
            entity_ids=entity_ids,
            missing=tuple(missing),
        )

    def _entities_for_area_label(
        self, area_id: str, label_id: str
    ) -> tuple[set[str], bool, bool]:
        """Return entity ids that are in an area AND carry a label.

        The label counts when it is on the entity itself or on the entity's
        device. Because the label is an explicit choice, config/diagnostic
        entities are not excluded here (hidden and disabled ones still are).
        """
        area_registry = ar.async_get(self.hass)
        label_registry = lr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        area_missing = area_registry.async_get_area(area_id) is None
        label_missing = label_registry.async_get_label(label_id) is None

        in_area: dict[str, er.RegistryEntry] = {}
        for entry in entity_registry.entities.get_entries_for_area_id(area_id):
            in_area[entry.entity_id] = entry
        for device in device_registry.devices.get_devices_for_area_id(area_id):
            for entry in entity_registry.entities.get_entries_for_device_id(device.id):
                if not entry.area_id:
                    in_area[entry.entity_id] = entry

        result: set[str] = set()
        for entry in in_area.values():
            if entry.hidden_by is not None or entry.disabled_by is not None:
                continue
            owner = (
                device_registry.async_get(entry.device_id) if entry.device_id else None
            )
            if label_id in entry.labels or (
                owner is not None and label_id in owner.labels
            ):
                result.add(entry.entity_id)

        return result, area_missing, label_missing

    def _entities_for_label(self, label_id: str) -> tuple[set[str], bool]:
        """Return entity ids carrying a label, using Home Assistant's own rules.

        Matches what a service call targeting a label would reach: entities
        labelled directly, entities of labelled devices, and entities in
        labelled areas. Hidden entities, and config/diagnostic entities that
        are only reached through a device or area, are skipped.
        """
        label_registry = lr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)

        missing = label_registry.async_get_label(label_id) is None
        result: set[str] = set()

        for entry in entity_registry.entities.get_entries_for_label(label_id):
            if entry.hidden_by is None and entry.disabled_by is None:
                result.add(entry.entity_id)

        for device in device_registry.devices.get_devices_for_label(label_id):
            result.update(self._device_entities(entity_registry, device.id))

        for area in area_registry.areas.get_areas_for_label(label_id):
            area_entities, _ = self._entities_for_area(area.id)
            result.update(area_entities)

        return result, missing

    def _entities_for_area(self, area_id: str) -> tuple[set[str], bool]:
        """Return entity ids in an area.

        An entity belongs to an area when it is assigned to it directly, or
        when its device is in the area and the entity has no area of its own.
        """
        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        missing = area_registry.async_get_area(area_id) is None
        result: set[str] = set()

        for entry in entity_registry.entities.get_entries_for_area_id(area_id):
            if self._is_usable(entry):
                result.add(entry.entity_id)

        for device in device_registry.devices.get_devices_for_area_id(area_id):
            result.update(
                self._device_entities(
                    entity_registry, device.id, require_no_area=True
                )
            )

        return result, missing

    def _device_entities(
        self,
        entity_registry: er.EntityRegistry,
        device_id: str,
        *,
        require_no_area: bool = False,
    ) -> set[str]:
        """Return the usable entity ids of a device."""
        return {
            entry.entity_id
            for entry in entity_registry.entities.get_entries_for_device_id(device_id)
            if self._is_usable(entry)
            and not (require_no_area and entry.area_id)
        }

    @staticmethod
    def _is_usable(entry: er.RegistryEntry) -> bool:
        """Return whether an entity should be included via device/area lookup."""
        return (
            entry.entity_category is None
            and entry.hidden_by is None
            and entry.disabled_by is None
        )
