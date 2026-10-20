"""Config flow for Dynamic Groups.

Setting up the integration only creates the (empty) integration entry. Groups
are then added from the integration page as subentries.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.core import callback

from .const import (
    DOMAIN,
    GROUP_TYPE_AREA_DOMAIN,
    GROUP_TYPE_AREA_LABEL,
    GROUP_TYPE_LABEL_DOMAIN,
)
from .subentry_flow import (
    AreaDomainSubentryFlow,
    AreaLabelSubentryFlow,
    LabelDomainSubentryFlow,
)


class DynamicGroupsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of Dynamic Groups."""

    VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the kinds of group that can be added."""
        return {
            GROUP_TYPE_LABEL_DOMAIN: LabelDomainSubentryFlow,
            GROUP_TYPE_AREA_DOMAIN: AreaDomainSubentryFlow,
            GROUP_TYPE_AREA_LABEL: AreaLabelSubentryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the integration; groups are added afterwards."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is None:
            return self.async_show_form(step_id="user")

        return self.async_create_entry(title="Dynamic Groups", data={})
