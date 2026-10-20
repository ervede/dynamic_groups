"""The Dynamic Groups integration.

Creates and maintains Home Assistant groups from labels and areas.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .const import (
    CONF_AREA,
    CONF_DOMAIN_FILTER,
    CONF_LABEL,
    CONF_NAME,
    DOMAIN,
    GROUP_TYPE_AREA_DOMAIN,
    GROUP_TYPE_LABEL_DOMAIN,
    LEGACY_CONF_GROUPS,
)
from .coordinator import DynamicGroupsCoordinator
from .group_manager import GroupManager, async_purge_registry_entries

_LOGGER = logging.getLogger(__name__)

# One platform per domain that Home Assistant has a group type for. Groups with
# any other domain filter fall back to plain group.* entities.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.MEDIA_PLAYER,
    Platform.SWITCH,
    Platform.VALVE,
]


@dataclass(slots=True)
class DynamicGroupsData:
    """Runtime data stored on the config entry."""

    coordinator: DynamicGroupsCoordinator
    manager: GroupManager


type DynamicGroupsConfigEntry = ConfigEntry[DynamicGroupsData]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate version 1 (groups kept in options) to subentries."""
    if entry.version > 2:
        return False

    if entry.version == 1:
        legacy = entry.options.get(LEGACY_CONF_GROUPS, [])
        for raw in legacy if isinstance(legacy, list) else []:
            try:
                is_label = raw["type"] == "label"
                name = str(raw["name"]).strip()
                data = {
                    CONF_NAME: name,
                    CONF_DOMAIN_FILTER: str(raw["domain"]),
                    (CONF_LABEL if is_label else CONF_AREA): str(raw["target"]),
                }
            except (KeyError, TypeError):
                _LOGGER.warning("Could not migrate malformed group: %s", raw)
                continue
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(data),
                    subentry_type=(
                        GROUP_TYPE_LABEL_DOMAIN if is_label else GROUP_TYPE_AREA_DOMAIN
                    ),
                    title=name,
                    unique_id=slugify(name),
                ),
            )
        hass.config_entries.async_update_entry(entry, options={}, version=2)
        _LOGGER.info("Migrated Dynamic Groups to configuration version 2")

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: DynamicGroupsConfigEntry
) -> bool:
    """Set up Dynamic Groups from a config entry."""
    coordinator = DynamicGroupsCoordinator(hass, entry)
    manager = GroupManager(hass)

    entry.runtime_data = DynamicGroupsData(coordinator=coordinator, manager=manager)

    # Raises ConfigEntryNotReady (and is retried by Home Assistant) on failure.
    await coordinator.async_config_entry_first_refresh()

    # The platforms only register themselves with the manager; the manager
    # then adds the entities, so it can choose typed or plain group entities.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_sync(coordinator.data)

    @callback
    def _async_apply_update() -> None:
        """Push the latest resolution to Home Assistant's groups."""
        if not coordinator.last_update_success:
            return
        entry.async_create_background_task(
            hass,
            manager.async_sync(coordinator.data),
            name=f"{DOMAIN} group sync",
        )

    # The coordinator only schedules its periodic refresh while it has at
    # least one listener, so this listener is what keeps the 10 s scan alive.
    entry.async_on_unload(coordinator.async_add_listener(_async_apply_update))
    coordinator.async_start_registry_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_groups_changed))

    _LOGGER.debug(
        "Dynamic Groups set up with %d configured groups", len(coordinator.data)
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DynamicGroupsConfigEntry
) -> bool:
    """Unload a config entry, taking its groups out of the state machine."""
    await entry.runtime_data.manager.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the group registry entries when the integration is deleted."""
    removed = async_purge_registry_entries(hass)
    if removed:
        _LOGGER.info("Removed %d group registry entries", removed)


async def _async_groups_changed(
    hass: HomeAssistant, entry: DynamicGroupsConfigEntry
) -> None:
    """Apply added, edited or deleted groups immediately (no reload needed)."""
    await entry.runtime_data.coordinator.async_refresh()
