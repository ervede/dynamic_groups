"""Create, update and remove the group entities for Dynamic Groups.

Each dynamic group is a regular ``group.*`` entity of Home Assistant's built-in
``group`` integration, created with a unique ID so it appears in the entity
registry and can be renamed, assigned to an area, hidden and so on from the UI.

The public ``group.set`` service cannot attach a unique ID, so entities are
created with the group integration's own ``Group`` class and entity component.
Those are internal to Home Assistant; if a future release changes them, group
creation fails with a clear error in the log instead of breaking anything else.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.group.entity import Group, async_get_component
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import UNIQUE_ID_PREFIX
from .coordinator import ResolvedGroup

_LOGGER = logging.getLogger(__name__)

GROUP_DOMAIN = "group"


def group_unique_id(group_id: str) -> str:
    """Return the entity unique ID for a group (subentry) id."""
    return f"{UNIQUE_ID_PREFIX}{group_id}"


def async_purge_registry_entries(
    hass: HomeAssistant, keep_group_ids: set[str] | None = None
) -> int:
    """Delete our group registry entries, except those of ``keep_group_ids``.

    Passing None deletes all of them. Returns the number removed.
    """
    registry = er.async_get(hass)
    keep = {group_unique_id(group_id) for group_id in keep_group_ids or ()}
    stale = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == GROUP_DOMAIN
        and entry.unique_id.startswith(UNIQUE_ID_PREFIX)
        and entry.unique_id not in keep
    ]
    for entity_id in stale:
        registry.async_remove(entity_id)
    return len(stale)


class GroupManager:
    """Keep Home Assistant group entities in sync with resolved definitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager."""
        self._hass = hass
        # group id (subentry id) -> the entity we added for it
        self._entities: dict[str, Group] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._purged_orphans = False

    async def async_sync(self, resolved: dict[str, ResolvedGroup]) -> None:
        """Make Home Assistant match the resolved groups."""
        async with self._lock:
            if self._closed:
                return

            if not self._purged_orphans:
                # Groups deleted while the integration was not running.
                removed = async_purge_registry_entries(self._hass, set(resolved))
                if removed:
                    _LOGGER.info("Removed %d orphaned group registry entries", removed)
                self._purged_orphans = True

            for group_id in sorted(set(self._entities) - set(resolved)):
                await self._async_delete(group_id)

            for group_id, item in resolved.items():
                await self._async_apply(group_id, item)

    async def async_unload(self) -> None:
        """Remove our entities from the state machine but keep registry entries.

        The entity registry entries (and with them any customisation the user
        made) survive, exactly as for any other integration being unloaded.
        """
        async with self._lock:
            # Stops queued syncs from re-creating groups after unload.
            self._closed = True
            for group_id in sorted(self._entities):
                await self._entities.pop(group_id).async_remove()

    async def _async_apply(self, group_id: str, item: ResolvedGroup) -> None:
        """Create or update one group entity."""
        entity = self._entities.get(group_id)

        if entity is not None and self._hass.states.get(entity.entity_id) is None:
            # Removed behind our back (for example deleted in the UI).
            _LOGGER.debug("Group %s disappeared; recreating it", entity.entity_id)
            self._entities.pop(group_id)
            entity = None

        if entity is None:
            await self._async_create(group_id, item)
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

    async def _async_create(self, group_id: str, item: ResolvedGroup) -> None:
        """Create a group entity."""
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

        self._entities[group_id] = entity
        _LOGGER.info(
            "Created group %s ('%s') with %d entities",
            entity.entity_id,
            definition.name,
            len(item.entity_ids),
        )

    async def _async_delete(self, group_id: str) -> None:
        """Delete a group the user no longer wants, including its registry entry."""
        entity = self._entities.pop(group_id)
        entity_id = entity.entity_id
        await entity.async_remove(force_remove=True)
        registry = er.async_get(self._hass)
        if registry.async_get(entity_id) is not None:
            registry.async_remove(entity_id)
        _LOGGER.info("Removed group %s (no longer configured)", entity_id)
