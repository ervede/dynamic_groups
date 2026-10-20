"""Valve platform: groups whose domain filter is "valve"."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DynamicGroupsConfigEntry
from .typed import async_register_typed_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicGroupsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register this platform; the group manager adds the entities."""
    await async_register_typed_platform(entry, "valve")
