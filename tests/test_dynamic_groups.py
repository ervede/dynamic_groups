"""Tests for Dynamic Groups against a real Home Assistant core."""

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dynamic_groups.const import DOMAIN


def _sub(stype: str, name: str, sid: str, **data) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={"name": name, **data},
        subentry_type=stype,
        title=name,
        unique_id=name.lower().replace(" ", "_"),
        subentry_id=sid,
    )


async def _setup(hass: HomeAssistant, subentries) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, version=2, subentries_data=subentries
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick(hass: HomeAssistant, seconds: int = 15) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def _ent(hass, domain, uid, **kw):
    reg = er.async_get(hass)
    e = reg.async_get_or_create(domain, "test", uid, suggested_object_id=uid)
    if kw:
        reg.async_update_entity(e.entity_id, **kw)
    # Real members always have a state; a group of stateless members is
    # unavailable, and unavailable entities carry no attributes.
    hass.states.async_set(e.entity_id, "off")
    return e.entity_id


def _members(hass: HomeAssistant, entity_id: str) -> set[str]:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return set(state.attributes["entity_id"])


async def test_typed_groups_unique_ids_members_and_state(hass: HomeAssistant) -> None:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    other = ar.async_get(hass).async_create("Other")
    night = lr.async_get(hass).async_create("Night")
    tv = lr.async_get(hass).async_create("TV")

    l1 = _ent(hass, "light", "l1", labels={night.label_id})
    s1 = _ent(hass, "switch", "s1", labels={night.label_id})
    l2 = _ent(hass, "light", "l2", area_id=area.id)
    l3 = _ent(hass, "light", "l3", area_id=area.id, labels={tv.label_id})
    s2 = _ent(hass, "switch", "s2", area_id=area.id, labels={tv.label_id})
    _ent(hass, "light", "l4", area_id=other.id, labels={tv.label_id})
    _ent(
        hass, "light", "l5", area_id=area.id, labels={tv.label_id},
        hidden_by=er.RegistryEntryHider.USER,
    )
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    dev = dev_reg.async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("test", "d")})
    dev_reg.async_update_device(dev.id, area_id=area.id, labels={tv.label_id})
    l6 = ent_reg.async_get_or_create("light", "test", "l6", suggested_object_id="l6", device_id=dev.id).entity_id
    hass.states.async_set(l6, "off")
    hass.states.async_set(l3, "on")

    entry = await _setup(
        hass,
        [
            _sub("label_domain", "Night Lights", "a", label=night.label_id, domain="light"),
            _sub("area_domain", "Kitchen Lights", "b", area=area.id, domain="light"),
            _sub("area_label", "Kitchen TV", "c", area=area.id, label=tv.label_id, domain=""),
            _sub("area_label", "Kitchen TV Lights", "d", area=area.id, label=tv.label_id, domain="light"),
            _sub("label_domain", "Night Switches", "e", label=night.label_id, domain="switch"),
        ],
    )
    assert entry.state is ConfigEntryState.LOADED

    # typed entities, like groups created through Settings > Helpers
    assert _members(hass, "light.night_lights") == {l1}
    assert _members(hass, "light.kitchen_lights") == {l2, l3, l6}
    assert _members(hass, "light.kitchen_tv_lights") == {l3, l6}
    assert _members(hass, "switch.night_switches") == {s1}
    assert hass.states.get("light.kitchen_lights").state == "on"
    assert hass.states.get("light.night_lights").state == "off"
    assert hass.states.get("light.night_lights").attributes["friendly_name"] == "Night Lights"

    # no domain filter: no matching group type, plain group entity as fallback
    assert _members(hass, "group.dg_kitchen_tv") == {l3, s2, l6}

    # every group has a unique ID and belongs to its own subentry
    expected = {
        "light.night_lights": "a",
        "light.kitchen_lights": "b",
        "group.dg_kitchen_tv": "c",
        "light.kitchen_tv_lights": "d",
        "switch.night_switches": "e",
    }
    for entity_id, sid in expected.items():
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None, entity_id
        assert reg_entry.unique_id == f"dynamic_groups_{sid}"
    for entity_id, sid in expected.items():
        if not entity_id.startswith("group."):
            assert ent_reg.async_get(entity_id).config_subentry_id == sid


async def test_unsupported_domain_falls_back_to_group(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("L")
    c1 = _ent(hass, "climate", "c1", labels={label.label_id})
    await _setup(hass, [_sub("label_domain", "Heaters", "a", label=label.label_id, domain="climate")])
    assert _members(hass, "group.dg_heaters") == {c1}
    assert er.async_get(hass).async_get("group.dg_heaters").unique_id == "dynamic_groups_a"


async def test_dynamic_updates_event_and_poll(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    tv = lr.async_get(hass).async_create("TV")
    l1 = _ent(hass, "light", "l1", area_id=area.id, labels={tv.label_id})
    await _setup(hass, [_sub("area_label", "Kitchen TV", "c", area=area.id, label=tv.label_id, domain="light")])
    assert _members(hass, "light.kitchen_tv") == {l1}

    l2 = _ent(hass, "light", "l2", area_id=area.id, labels={tv.label_id})
    await hass.async_block_till_done()
    await _tick(hass, 5)  # debounced registry-event refresh
    assert _members(hass, "light.kitchen_tv") == {l1, l2}

    er.async_get(hass).async_update_entity(l2, labels=set())
    await hass.async_block_till_done()
    await _tick(hass, 15)
    assert _members(hass, "light.kitchen_tv") == {l1}


async def test_user_customisations_survive_updates_and_reload(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("L")
    l1 = _ent(hass, "light", "l1", labels={label.label_id})
    entry = await _setup(hass, [_sub("label_domain", "My Lights", "a", label=label.label_id, domain="light")])
    reg = er.async_get(hass)

    # user renames the entity id and name in the UI
    reg.async_update_entity("light.my_lights", new_entity_id="light.renamed", name="Custom Name")
    await hass.async_block_till_done()
    assert hass.states.get("light.renamed").attributes["friendly_name"] == "Custom Name"

    # membership changes: entity is rebuilt but keeps id and customisation
    l2 = _ent(hass, "light", "l2", labels={label.label_id})
    await hass.async_block_till_done()
    await _tick(hass, 15)
    assert _members(hass, "light.renamed") == {l1, l2}
    assert hass.states.get("light.renamed").attributes["friendly_name"] == "Custom Name"
    assert hass.states.get("light.my_lights") is None
    assert hass.states.get("light.renamed_2") is None

    # unload: entity becomes unavailable, registry entry kept
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.renamed").state == "unavailable"
    assert reg.async_get("light.renamed") is not None

    # reload: same entity id, customisation intact, no duplicate
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("light.renamed")
    assert state.state != "unavailable"
    assert state.attributes["friendly_name"] == "Custom Name"
    assert hass.states.get("light.renamed_2") is None


async def test_subentry_add_edit_delete_via_flows(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    tv = lr.async_get(hass).async_create("TV")
    l1 = _ent(hass, "light", "l1", area_id=area.id, labels={tv.label_id})
    entry = await _setup(hass, [])

    # --- add an area + label group, no domain -> plain group entity
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area_label"), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"area": area.id, "label": tv.label_id, "domain": "Bad Domain!", "name": "!!"},
    )
    assert result["errors"] == {"domain": "invalid_domain", "name": "invalid_name"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"area": area.id, "label": tv.label_id, "name": "Kitchen TV"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _members(hass, "group.dg_kitchen_tv") == {l1}

    # --- duplicate name rejected; new area + domain group becomes a light group
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area_domain"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"area": area.id, "domain": "Light", "name": "kitchen tv"}
    )
    assert result["errors"] == {"name": "name_exists"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"area": area.id, "domain": "Light", "name": "Kitchen Lights"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _members(hass, "light.kitchen_lights") == {l1}

    # --- reconfigure the plain group to a switch domain: becomes a switch group
    sub = next(s for s in entry.subentries.values() if s.title == "Kitchen TV")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area_label"),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"area": area.id, "label": tv.label_id, "domain": "switch", "name": "Kitchen TV"},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert hass.states.get("group.dg_kitchen_tv") is None
    assert er.async_get(hass).async_get("group.dg_kitchen_tv") is None
    assert hass.states.get("switch.kitchen_tv") is not None

    # --- reconfigure to an unsupported domain: back to a plain group, switch gone
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area_label"),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"area": area.id, "label": tv.label_id, "domain": "climate", "name": "Kitchen TV"},
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.kitchen_tv") is None
    assert er.async_get(hass).async_get("switch.kitchen_tv") is None
    assert hass.states.get("group.dg_kitchen_tv") is not None

    # --- delete: entities and registry entries vanish
    hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
    await hass.async_block_till_done()
    assert hass.states.get("group.dg_kitchen_tv") is None
    assert er.async_get(hass).async_get("group.dg_kitchen_tv") is None
    other = next(s for s in entry.subentries.values())
    hass.config_entries.async_remove_subentry(entry, other.subentry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.kitchen_lights") is None
    assert er.async_get(hass).async_get("light.kitchen_lights") is None


async def test_flow_when_no_areas_or_labels(hass: HomeAssistant) -> None:
    entry = await _setup(hass, [])
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area_label"), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_areas"}


async def test_config_flow_creates_empty_entry_once(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert not entry.subentries

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT


async def test_migration_from_v1_options(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("Night")
    area = ar.async_get(hass).async_create("Kitchen")
    l1 = _ent(hass, "light", "l1", labels={label.label_id})
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, version=1,
        options={"groups": [
            {"id": "x", "type": "label", "name": "Night Lights", "target": label.label_id, "domain": "light"},
            {"id": "y", "type": "area", "name": "Kitchen Switches", "target": area.id, "domain": "switch"},
        ]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2 and entry.options == {}
    assert sorted(s.subentry_type for s in entry.subentries.values()) == ["area_domain", "label_domain"]
    assert _members(hass, "light.night_lights") == {l1}
    assert hass.states.get("switch.kitchen_switches") is not None


async def test_upgrade_from_2_0_replaces_old_group_entities(hass: HomeAssistant) -> None:
    """2.0.x created group.dg_* entities; they are replaced by typed ones."""
    label = lr.async_get(hass).async_create("L")
    l1 = _ent(hass, "light", "l1", labels={label.label_id})
    reg = er.async_get(hass)
    reg.async_get_or_create(
        "group", "group", "dynamic_groups_a", suggested_object_id="dg_my_lights"
    )
    reg.async_get_or_create(
        "group", "group", "dynamic_groups_stale", suggested_object_id="dg_stale"
    )
    await _setup(hass, [
        _sub("label_domain", "My Lights", "a", label=label.label_id, domain="light"),
        _sub("label_domain", "Heaters", "b", label=label.label_id, domain="climate"),
    ])
    assert reg.async_get("group.dg_my_lights") is None  # superseded
    assert reg.async_get("group.dg_stale") is None  # orphan
    assert _members(hass, "light.my_lights") == {l1}
    assert reg.async_get("group.dg_heaters") is not None  # still a plain group


async def test_missing_target_warns_once(hass: HomeAssistant, caplog) -> None:
    await _setup(hass, [_sub("area_label", "Ghost", "g", area="nope", label="nada", domain="")])
    assert hass.states.get("group.dg_ghost").attributes["entity_id"] == ()
    for _ in range(3):
        await _tick(hass, 15)
    assert caplog.text.count("does not exist") == 1


async def test_removing_integration_purges_registry(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("L")
    _ent(hass, "light", "l1", labels={label.label_id})
    entry = await _setup(hass, [
        _sub("label_domain", "One", "a", label=label.label_id, domain="light"),
        _sub("label_domain", "Two", "b", label=label.label_id, domain="climate"),
    ])
    reg = er.async_get(hass)
    assert reg.async_get("light.one") is not None
    assert reg.async_get("group.dg_two") is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get("light.one") is None
    assert reg.async_get("group.dg_two") is None
    assert hass.states.get("light.one") is None
    assert hass.states.get("group.dg_two") is None


async def test_group_deleted_while_unloaded_is_purged_and_disabled_respected(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("L")
    _ent(hass, "light", "l1", labels={label.label_id})
    entry = await _setup(hass, [
        _sub("label_domain", "One", "a", label=label.label_id, domain="light"),
        _sub("label_domain", "Two", "b", label=label.label_id, domain="light"),
    ])
    reg = er.async_get(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_remove_subentry(entry, "b")
    reg.async_update_entity("light.one", disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get("light.two") is None  # removed with its subentry
    # disabled: never revived as a live entity
    state = hass.states.get("light.one")
    assert state is None or state.state == "unavailable"
    await _tick(hass, 15)
    state = hass.states.get("light.one")
    assert state is None or state.state == "unavailable"


async def test_entity_deleted_behind_our_back_is_recreated(hass: HomeAssistant) -> None:
    label = lr.async_get(hass).async_create("L")
    _ent(hass, "light", "l1", labels={label.label_id})
    entry = await _setup(hass, [_sub("label_domain", "One", "a", label=label.label_id, domain="light")])
    await entry.runtime_data.manager._typed["a"].entity.async_remove(force_remove=True)
    assert hass.states.get("light.one") is None
    await _tick(hass, 15)
    assert hass.states.get("light.one") is not None


@pytest.mark.parametrize(
    "domain",
    ["binary_sensor", "cover", "fan", "light", "lock", "media_player", "switch", "valve"],
)
async def test_every_supported_domain_creates_a_typed_group(
    hass: HomeAssistant, caplog, domain: str
) -> None:
    label = lr.async_get(hass).async_create("L")
    member = _ent(hass, domain, "m1", labels={label.label_id})
    entry = await _setup(
        hass, [_sub("label_domain", "Grp", "a", label=label.label_id, domain=domain)]
    )
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(f"{domain}.grp")
    assert state is not None, caplog.text
    assert state.state != "unavailable"
    assert _members(hass, f"{domain}.grp") == {member}
    assert hass.states.get("group.dg_grp") is None
    assert "Failed to create" not in caplog.text
    reg_entry = er.async_get(hass).async_get(f"{domain}.grp")
    assert reg_entry.unique_id == "dynamic_groups_a"
    assert reg_entry.config_subentry_id == "a"
