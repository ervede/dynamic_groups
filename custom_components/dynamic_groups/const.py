"""Constants for the Dynamic Groups integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "dynamic_groups"

# Subentry types (one subentry = one dynamic group). The value is also the
# subentry_type used in translations.
GROUP_TYPE_LABEL_DOMAIN: Final = "label_domain"
GROUP_TYPE_AREA_DOMAIN: Final = "area_domain"
GROUP_TYPE_AREA_LABEL: Final = "area_label"
GROUP_TYPES: Final[tuple[str, ...]] = (
    GROUP_TYPE_LABEL_DOMAIN,
    GROUP_TYPE_AREA_DOMAIN,
    GROUP_TYPE_AREA_LABEL,
)

# Keys inside a subentry's data
CONF_NAME: Final = "name"
CONF_AREA: Final = "area"
CONF_LABEL: Final = "label"
CONF_DOMAIN_FILTER: Final = "domain"

# Version 1 stored groups as a list in the entry options.
LEGACY_CONF_GROUPS: Final = "groups"

# Unique ID of the group entity: UNIQUE_ID_PREFIX + subentry_id
UNIQUE_ID_PREFIX: Final = "dynamic_groups_"

# Prefix for every group object_id created by this integration. It keeps our
# groups from ever colliding with (and overwriting) groups the user defined
# elsewhere.
OBJECT_ID_PREFIX: Final = "dg_"

# Refresh interval requested by the specification.
UPDATE_INTERVAL: Final = timedelta(seconds=10)

# Short delay used to coalesce bursts of registry events into one refresh.
REGISTRY_REFRESH_COOLDOWN: Final = 2.0

# Domains offered in the dropdown. The selector also accepts a custom value.
SUPPORTED_DOMAINS: Final[tuple[str, ...]] = (
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "camera",
    "climate",
    "cover",
    "device_tracker",
    "fan",
    "humidifier",
    "input_boolean",
    "light",
    "lock",
    "media_player",
    "number",
    "remote",
    "scene",
    "select",
    "sensor",
    "siren",
    "switch",
    "update",
    "vacuum",
    "valve",
    "water_heater",
)
