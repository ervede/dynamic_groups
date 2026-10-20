"""Create, update and remove the group entities for Dynamic Groups.

A group whose domain filter has a matching Home Assistant group type (light,
switch, binary_sensor, cover, fan, lock, media_player, valve) becomes a typed
entity such as ``light.living_room``, exactly like a group made through
Settings > Helpers. Every other group (another domain, or no domain filter)
falls back to a ``group.*`` entity.

All entities are registered with a unique ID, so they can be renamed, moved to
an area, hidden or disabled from the UI, and those changes survive updates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from homeassistant.components.group.entity import Group, async_get_component
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from .const import DOMAIN, UNIQUE_ID_PREFIX
from .coordinator import ResolvedGroup
from .typed import GroupFactory

_LOGGER = logging.getLogger(__name__)

GROUP_DOMAIN = "group"


def group_unique_id(group_id: str) -> str:
    """Return the entity unique ID for a group (subentry) id."""
    return f"{UNIQUE_ID_PREFIX}{group_id}"


def async_purge_registry_entries(
    hass: HomeAssistant, keep_group_ids: set[str] | None = None
) -> int:
    """Delete our ``group.*`` registry entries, except those of ``keep_group_ids``.

    Passing None deletes all of them. Returns the number removed. Typed
    entities belong to the config entry and are cleaned up by Home Assistant.
    """
    registry = er.async_get(hass)
    keep = {group_unique_id(group_id) for group_id in keep_group_ids or ()}
    stale = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == GROUP_DOMAIN
        and entry.platform == GROUP_DOMAIN
        and entry.unique_id.startswith(UNIQUE_ID_PREFIX)
        and entry.unique_id not in keep
    ]
    for entity_id in stale:
        registry.async_remove(entity_id)
    return len(stale)


@dataclass(frozen=True, slots=True)
class _Platform:
    """An entity platform that can host typed groups."""

    platform: EntityPlatform
    factory: GroupFactory


@dataclass(slots=True)
class _TypedGroup:
    """A typed group entity we added."""

    entity: Entity
    domain: str
    name: str
    members: tuple[str, ...]


class GroupManager:
    """Keep Home Assistant group entities in sync with resolved definitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._platforms: dict[str, _Platform] = {}
        self._typed: dict[str, _TypedGroup] = {}
        self._legacy: dict[str, Group] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._purged_orphans = False

    def register_platform(
        self, domain: str, platform: EntityPlatform, factory: GroupFactory
    ) -> None:
        """Called by each typed platform when it is set up."""
        self._platforms[domain] = _Platform(platform, factory)

    async def async_sync(self, resolved: dict[str, ResolvedGroup]) -> None:
        """Make Home Assistant match the resolved groups."""
        async with self._lock:
            if self._closed:
                return

            if not self._purged_orphans:
                # Old group.* entries: groups deleted while we were not running,
                # and groups that are typed entities now (upgrade from 2.0.x).
                keep = {
                    group_id
                    for group_id, item in resolved.items()
                    if item.definition.domain not in self._platforms
                }
                removed = async_purge_registry_entries(self._hass, keep)
                if removed:
                    _LOGGER.info("Removed %d outdated group registry entries", removed)
                self._purged_orphans = True

            for group_id in sorted((set(self._legacy) | set(self._typed)) - set(resolved)):
                await self._async_delete(group_id)

            for group_id, item in resolved.items():
                await self._async_apply(group_id, item)

    async def async_unload(self) -> None:
        """Take our entities out of the state machine, keeping registry entries.

        Typed entities are removed by Home Assistant when the platforms unload.
        """
        async with self._lock:
            # Stops queued syncs from re-creating groups after unload.
            self._closed = True
            for group_id in sorted(self._legacy):
                await self._legacy.pop(group_id).async_remove()
            self._typed.clear()

    # -- dispatch ---------------------------------------------------------------

    async def _async_apply(self, group_id: str, item: ResolvedGroup) -> None:
        """Create or update one group in whichever form it needs."""
        domain = item.definition.domain
        typed = domain in self._platforms

        existing_typed = self._typed.get(group_id)
        if existing_typed is not None and (not typed or existing_typed.domain != domain):
            await self._async_delete_typed(group_id)
        if typed and group_id in self._legacy:
            await self._async_delete_legacy(group_id)

        if typed:
            await self._async_apply_typed(group_id, item)
        else:
            await self._async_apply_legacy(group_id, item)

    async def _async_delete(self, group_id: str) -> None:
        """Delete a group the user no longer wants, including registry entries."""
        if group_id in self._typed:
            await self._async_delete_typed(group_id)
        if group_id in self._legacy:
            await self._async_delete_legacy(group_id)

    # -- typed entities ---------------------------------------------------------

    async def _async_apply_typed(self, group_id: str, item: ResolvedGroup) -> None:
        existing = self._typed.get(group_id)

        if existing is not None and (
            existing.entity.hass is None
            or existing.entity.entity_id is None
            or self._hass.states.get(existing.entity.entity_id) is None
        ):
            # Removed behind our back; recreate it.
            _LOGGER.debug("Group %s disappeared; recreating it", existing.entity.entity_id)
            self._typed.pop(group_id)
            existing = None

        if existing is None:
            await self._async_create_typed(group_id, item)
            return

        name = item.definition.name
        if existing.members == item.entity_ids and existing.name == name:
            return

        # Membership is fixed when the core group entity is built, so replace
        # the entity. Keeping the unique ID means the same entity_id and all
        # user customisations carry over.
        entity_id = existing.entity.entity_id
        previous = len(existing.members)
        self._typed.pop(group_id)
        await existing.entity.async_remove()
        if await self._async_create_typed(group_id, item, log=False):
            _LOGGER.info(
                "Updated group %s ('%s'): %d -> %d entities",
                entity_id,
                name,
                previous,
                len(item.entity_ids),
            )

    async def _async_create_typed(
        self, group_id: str, item: ResolvedGroup, *, log: bool = True
    ) -> bool:
        definition = item.definition
        platform = self._platforms[definition.domain]
        unique_id = group_unique_id(group_id)

        registry = er.async_get(self._hass)
        existing_id = registry.async_get_entity_id(definition.domain, DOMAIN, unique_id)
        if existing_id is not None:
            registered = registry.async_get(existing_id)
            if registered is not None and registered.disabled:
                _LOGGER.debug("Group %s is disabled; not creating it", existing_id)
                return False

        entity = platform.factory(unique_id, definition.name, list(item.entity_ids))
        try:
            await platform.platform.async_add_entities(
                [entity], config_subentry_id=group_id
            )
        except Exception:  # noqa: BLE001 - never let one group break the rest
            _LOGGER.exception("Failed to create group '%s'", definition.name)
            return False

        if (
            entity.hass is None
            or entity.entity_id is None
            or self._hass.states.get(entity.entity_id) is None
        ):
            _LOGGER.error("Group '%s' was not added to Home Assistant", definition.name)
            return False

        self._typed[group_id] = _TypedGroup(
            entity=entity,
            domain=definition.domain,
            name=definition.name,
            members=item.entity_ids,
        )
        if log:
            _LOGGER.info(
                "Created group %s ('%s') with %d entities",
                entity.entity_id,
                definition.name,
                len(item.entity_ids),
            )
        return True

    async def _async_delete_typed(self, group_id: str) -> None:
        typed = self._typed.pop(group_id)
        entity_id = typed.entity.entity_id
        if typed.entity.hass is not None:
            await typed.entity.async_remove(force_remove=True)
        registry = er.async_get(self._hass)
        if entity_id is not None and registry.async_get(entity_id) is not None:
            registry.async_remove(entity_id)
        _LOGGER.info("Removed group %s (no longer configured)", entity_id)

    # -- legacy group.* entities ------------------------------------------------

    async def _async_apply_legacy(self, group_id: str, item: ResolvedGroup) -> None:
        """Create or update a plain ``group.*`` entity."""
        entity = self._legacy.get(group_id)

        if entity is not None and self._hass.states.get(entity.entity_id) is None:
            _LOGGER.debug("Group %s disappeared; recreating it", entity.entity_id)
            self._legacy.pop(group_id)
            entity = None

        if entity is None:
            await self._async_create_legacy(group_id, item)
            return

        name = item.definition.name
        members = tuple(entity.tracking)
        changed_name = entity.name != name
        if members == item.entity_ids and not changed_name:
            return

        if members != item.entity_ids:
            entity.async_update_tracked_entity_ids(item.entity_ids)
        if changed_name:
            entity.set_name(name)
            entity.async_write_ha_state()
        _LOGGER.info(
            "Updated group %s ('%s'): %d -> %d entities",
            entity.entity_id,
            name,
            len(members),
            len(item.entity_ids),
        )

    async def _async_create_legacy(self, group_id: str, item: ResolvedGroup) -> None:
        unique_id = group_unique_id(group_id)
        registry = er.async_get(self._hass)
        existing_id = registry.async_get_entity_id(GROUP_DOMAIN, GROUP_DOMAIN, unique_id)
        if existing_id is not None:
            existing = registry.async_get(existing_id)
            if existing is not None and existing.disabled:
                _LOGGER.debug("Group %s is disabled; not creating it", existing_id)
                return

        definition = item.definition
        try:
            entity = Group.async_create_group_entity(
                self._hass,
                definition.name,
                created_by_service=True,
                entity_ids=item.entity_ids or None,
                icon=None,
                mode=None,
                object_id=definition.object_id,
                order=None,
            )
            entity._attr_unique_id = unique_id  # noqa: SLF001
            await async_get_component(self._hass).async_add_entities([entity])
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to create group '%s'. Home Assistant's group integration "
                "may have changed; please report this",
                definition.name,
            )
            return

        if self._hass.states.get(entity.entity_id) is None:
            _LOGGER.error(
                "Group '%s' was not added to Home Assistant (entity %s)",
                definition.name,
                entity.entity_id,
            )
            return

        self._legacy[group_id] = entity
        _LOGGER.info(
            "Created group %s ('%s') with %d entities",
            entity.entity_id,
            definition.name,
            len(item.entity_ids),
        )

    async def _async_delete_legacy(self, group_id: str) -> None:
        entity = self._legacy.pop(group_id)
        entity_id = entity.entity_id
        await entity.async_remove(force_remove=True)
        registry = er.async_get(self._hass)
        if registry.async_get(entity_id) is not None:
            registry.async_remove(entity_id)
        _LOGGER.info("Removed group %s (no longer configured)", entity_id)
