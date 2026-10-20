"""Subentry flows: each dynamic group is one config subentry.

The integration page shows one "Add ..." button per group type, and every
existing group can be reconfigured or deleted from its own menu.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import area_registry as ar, label_registry as lr
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.util import slugify

from .const import (
    CONF_AREA,
    CONF_DOMAIN_FILTER,
    CONF_LABEL,
    CONF_NAME,
    GROUP_TYPE_AREA_DOMAIN,
    GROUP_TYPE_AREA_LABEL,
    GROUP_TYPE_LABEL_DOMAIN,
    SUPPORTED_DOMAINS,
)
from .coordinator import is_valid_group_name

_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _area_options(hass_areas: ar.AreaRegistry) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=area.id, label=area.name)
        for area in sorted(
            hass_areas.async_list_areas(), key=lambda area: area.name.casefold()
        )
    ]


def _label_options(hass_labels: lr.LabelRegistry) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=label.label_id, label=label.name)
        for label in sorted(
            hass_labels.async_list_labels(), key=lambda label: label.name.casefold()
        )
    ]


class _GroupSubentryFlow(ConfigSubentryFlow):
    """Shared add / reconfigure logic for all group types."""

    group_type: str

    # -- steps -----------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a group."""
        return await self._async_handle("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing group."""
        return await self._async_handle("reconfigure", user_input)

    # -- implementation --------------------------------------------------------

    async def _async_handle(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        editing: ConfigSubentry | None = None
        if self.source == SOURCE_RECONFIGURE:
            editing = self._get_reconfigure_subentry()

        areas = ar.async_get(self.hass)
        labels = lr.async_get(self.hass)
        needs_area = self.group_type in (GROUP_TYPE_AREA_DOMAIN, GROUP_TYPE_AREA_LABEL)
        needs_label = self.group_type in (
            GROUP_TYPE_LABEL_DOMAIN,
            GROUP_TYPE_AREA_LABEL,
        )

        if (needs_area and not areas.areas) or (needs_label and not labels.labels):
            if user_input is not None:
                return self.async_abort(reason="nothing_to_choose")
            return self.async_show_form(
                step_id=step_id,
                data_schema=vol.Schema({}),
                errors={"base": "no_areas" if needs_area and not areas.areas else "no_labels"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = self._validate(entry, editing, user_input)
            if data is not None:
                title = data[CONF_NAME]
                unique_id = slugify(title)
                if editing is not None:
                    return self.async_update_and_abort(
                        entry, editing, title=title, data=data, unique_id=unique_id
                    )
                return self.async_create_entry(
                    title=title, data=data, unique_id=unique_id
                )

        suggested: dict[str, Any] | None = user_input
        if suggested is None and editing is not None:
            suggested = dict(editing.data)

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                self._schema(areas, labels), suggested
            ),
            errors=errors,
        )

    def _schema(self, areas: ar.AreaRegistry, labels: lr.LabelRegistry) -> vol.Schema:
        fields: dict[Any, Any] = {}
        if self.group_type in (GROUP_TYPE_AREA_DOMAIN, GROUP_TYPE_AREA_LABEL):
            fields[vol.Required(CONF_AREA)] = SelectSelector(
                SelectSelectorConfig(
                    options=_area_options(areas), mode=SelectSelectorMode.DROPDOWN
                )
            )
        if self.group_type in (GROUP_TYPE_LABEL_DOMAIN, GROUP_TYPE_AREA_LABEL):
            fields[vol.Required(CONF_LABEL)] = SelectSelector(
                SelectSelectorConfig(
                    options=_label_options(labels), mode=SelectSelectorMode.DROPDOWN
                )
            )
        domain_key = (
            vol.Optional(CONF_DOMAIN_FILTER)
            if self.group_type == GROUP_TYPE_AREA_LABEL
            else vol.Required(CONF_DOMAIN_FILTER)
        )
        fields[domain_key] = SelectSelector(
            SelectSelectorConfig(
                options=list(SUPPORTED_DOMAINS),
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )
        fields[vol.Required(CONF_NAME)] = TextSelector()
        return vol.Schema(fields)

    def _validate(
        self,
        entry: ConfigEntry,
        editing: ConfigSubentry | None,
        user_input: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        errors: dict[str, str] = {}
        data: dict[str, Any] = {}

        if self.group_type in (GROUP_TYPE_AREA_DOMAIN, GROUP_TYPE_AREA_LABEL):
            area_id = str(user_input[CONF_AREA])
            if ar.async_get(self.hass).async_get_area(area_id) is None:
                errors[CONF_AREA] = "unknown_area"
            data[CONF_AREA] = area_id

        if self.group_type in (GROUP_TYPE_LABEL_DOMAIN, GROUP_TYPE_AREA_LABEL):
            label_id = str(user_input[CONF_LABEL])
            if lr.async_get(self.hass).async_get_label(label_id) is None:
                errors[CONF_LABEL] = "unknown_label"
            data[CONF_LABEL] = label_id

        domain = str(user_input.get(CONF_DOMAIN_FILTER) or "").strip().lower()
        if domain:
            if not _DOMAIN_RE.fullmatch(domain):
                errors[CONF_DOMAIN_FILTER] = "invalid_domain"
        elif self.group_type != GROUP_TYPE_AREA_LABEL:
            errors[CONF_DOMAIN_FILTER] = "invalid_domain"
        data[CONF_DOMAIN_FILTER] = domain

        name = str(user_input[CONF_NAME]).strip()
        if not is_valid_group_name(name):
            errors[CONF_NAME] = "invalid_name"
        else:
            wanted = slugify(name)
            for other in entry.subentries.values():
                if editing is not None and other.subentry_id == editing.subentry_id:
                    continue
                if slugify(str(other.data.get(CONF_NAME, ""))) == wanted:
                    errors[CONF_NAME] = "name_exists"
                    break
        data[CONF_NAME] = name

        return (None, errors) if errors else (data, {})


class LabelDomainSubentryFlow(_GroupSubentryFlow):
    """Entities that carry a label, limited to one domain."""

    group_type = GROUP_TYPE_LABEL_DOMAIN


class AreaDomainSubentryFlow(_GroupSubentryFlow):
    """Entities in an area, limited to one domain."""

    group_type = GROUP_TYPE_AREA_DOMAIN


class AreaLabelSubentryFlow(_GroupSubentryFlow):
    """Entities in an area that also carry a label (domain optional)."""

    group_type = GROUP_TYPE_AREA_LABEL
