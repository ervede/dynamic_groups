"""Typed group entities.

Home Assistant's "Group" helper creates real light, switch, binary_sensor,
cover, ... entities. Those show history, activity and clickable members in
their dialog, unlike the old ``group.*`` entities. This module builds the same
entities with the group integration's own classes, so they behave exactly like
groups created through Settings > Helpers.

Those classes are internal to Home Assistant. If one cannot be imported (for
example after a future release moves it) that domain is skipped, an error is
logged, and its groups fall back to plain ``group.*`` entities.
"""

from __future__ import annotations

from collections.abc import Callable
import importlib
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import Entity

if TYPE_CHECKING:
    from . import DynamicGroupsConfigEntry

_LOGGER = logging.getLogger(__name__)

# factory(unique_id, name, member_entity_ids) -> entity
GroupFactory = Callable[[str, str, list[str]], Entity]

# domain -> (module in homeassistant.components.group, class name).
#
# light, switch and binary_sensor groups take an "all members" flag (False
# means on when any member is on); binary_sensor also takes a device class.
_SPECS: dict[str, tuple[str, str]] = {
    "binary_sensor": ("binary_sensor", "BinarySensorGroup"),
    "cover": ("cover", "CoverGroup"),
    "fan": ("fan", "FanGroup"),
    "light": ("light", "LightGroup"),
    "lock": ("lock", "LockGroup"),
    "media_player": ("media_player", "MediaPlayerGroup"),
    "switch": ("switch", "SwitchGroup"),
    "valve": ("valve", "ValveGroup"),
}


def build_factory(domain: str) -> GroupFactory | None:
    """Return a factory for ``domain`` groups, or None if unavailable."""
    module_name, class_name = _SPECS[domain]
    try:
        module = importlib.import_module(
            f"homeassistant.components.group.{module_name}"
        )
        group_class = getattr(module, class_name)
    except (ImportError, AttributeError) as err:
        _LOGGER.error(
            "Cannot create %s groups: Home Assistant's %s changed (%s). "
            "These groups will be plain group.* entities instead",
            domain,
            class_name,
            err,
        )
        return None

    if domain in ("light", "switch"):

        def make_with_mode(unique_id: str, name: str, ids: list[str]) -> Entity:
            entity: Entity = group_class(unique_id, name, ids, False)
            return entity

        return make_with_mode

    if domain == "binary_sensor":

        def make_binary_sensor(unique_id: str, name: str, ids: list[str]) -> Entity:
            entity: Entity = group_class(unique_id, name, None, ids, False)
            return entity

        return make_binary_sensor

    def make_plain(unique_id: str, name: str, ids: list[str]) -> Entity:
        entity: Entity = group_class(unique_id, name, ids)
        return entity

    return make_plain


async def async_register_typed_platform(
    entry: DynamicGroupsConfigEntry, domain: str
) -> None:
    """Hand a platform's entity adder to the group manager."""
    factory = build_factory(domain)
    if factory is None:
        return
    entry.runtime_data.manager.register_platform(
        domain, entity_platform.async_get_current_platform(), factory
    )
