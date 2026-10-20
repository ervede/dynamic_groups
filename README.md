# Dynamic Groups

[![Add custom repository to HACS](https://img.shields.io/badge/HACS-Add%20custom%20repository-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ervede&repository=dynamic_groups&category=integration)
[![Validate](https://img.shields.io/github/actions/workflow/status/ervede/dynamic_groups/validate.yml?branch=main&label=validate&style=for-the-badge)](https://github.com/ervede/dynamic_groups/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/ervede/dynamic_groups?style=for-the-badge)](https://github.com/ervede/dynamic_groups/releases)

Home Assistant integration that creates and maintains `group.*` entities from
**labels**, **areas** and **domains**, and keeps them up to date as your setup
changes. Everything is configured in the UI, no YAML.

## Installation

### HACS (recommended)

1. Click the **Add custom repository** button above. It opens your Home
   Assistant and offers to add this repository to HACS.
   <details>
   <summary>Or add it manually</summary>

   HACS > three-dot menu > **Custom repositories** > repository
   `https://github.com/ervede/dynamic_groups`, type **Integration** > Add.
   </details>
2. In HACS, open **Dynamic Groups** and click **Download**.
3. **Restart Home Assistant.**
4. Settings > Devices & services > **Add integration** > *Dynamic Groups*.
   This only creates the integration (one click, no questions).
5. Open the integration and use the **Add** buttons to create groups.

### Manual

Copy `custom_components/dynamic_groups` into your `config/custom_components/`
folder and restart Home Assistant.

Requires Home Assistant **2025.3 or newer** (config subentries).

## Group types

| Button | Members | Fields |
|---|---|---|
| Label + domain group | entities with the label, of one domain | label, domain, name |
| Area + domain group | entities in the area, of one domain | area, domain, name |
| Area + label group | entities in the area that also have the label | area, label, domain (optional), name |

Each group has its own entry in the integration's list with **Reconfigure** and
**Delete**. Changes apply immediately, without reloading the integration.

Groups are named `group.dg_<name>`, for example "Living Room Lights" gives
`group.dg_living_room_lights`.

## Unique IDs

Every group is registered in the entity registry with a unique ID, so the
settings dialog works: rename it, change its entity ID, set an area, hide or
disable it. These customisations survive restarts, reloads and membership
updates. Deleting a group in the integration also removes its registry entry.

## How membership is decided

* Every 10 seconds all groups are re-resolved, and also (debounced to 2 s) as
  soon as the entity, device, area or label registry changes.
* **Area**: entities assigned to the area, plus entities of devices in the area
  that have no area of their own. Hidden and disabled entities and
  config/diagnostic entities are skipped.
* **Label**: entities with the label, entities of devices with the label and
  entities in areas with the label (the same as targeting a label in a service
  call).
* **Area + label**: the entity must be in the area *and* have the label itself
  or on its device. Because you picked the label explicitly,
  config/diagnostic entities are allowed here; hidden and disabled still are not.
* A deleted label or area leaves the group empty and logs one warning.

## Notes on how groups are created

Home Assistant's public `group.set` service cannot attach a unique ID, so the
integration creates group entities with the `group` integration's own `Group`
class. That is an internal API; if a future Home Assistant release changes it,
group creation fails with a clear error in the log and nothing else is affected.

## Upgrading from 1.0.0

Version 1.0.0 stored groups in the integration's options. They are migrated to
the new per-group entries automatically on first start. The old groups had no
unique ID, so the new entities are created fresh as `group.dg_<name>`.

## Troubleshooting

* **Nothing created**: check Settings > System > Logs for `dynamic_groups`.
* **Group is empty**: confirm the entities really have the label/area, are not
  hidden or disabled, and match the domain. A warning names a deleted label or
  area.
* **Add button says there are no areas/labels**: create one under Settings >
  Areas, labels & zones first.
* **A disabled group shows as unavailable**: that is how Home Assistant shows
  disabled entities; the integration leaves it alone.
* **Debug logging**:
  ```yaml
  logger:
    logs:
      custom_components.dynamic_groups: debug
  ```

Found a bug? [Open an issue](https://github.com/ervede/dynamic_groups/issues).

## Development

```
pip install -r requirements_test.txt
pytest tests
```

## License

[MIT](LICENSE)
