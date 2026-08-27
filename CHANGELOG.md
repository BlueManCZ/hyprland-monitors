# Changelog

All notable changes to hyprland-monitors will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Clearing a mirror now works on live apply** — `lines_from_monitors(..., for_live_apply=True)` emits an explicit empty `mirror` value when `mirror_of` is cleared. Hyprland's additive Lua monitor rules otherwise retain the previous mirror target, so switching a display from Mirror back to Extend had no effect until a full config reload.

## [0.9.0] - 2026-08-08

### Fixed

- **Cleared overrides never reset live** — `lines_from_monitors` omitted `transform`, `bitdepth` and `cm` when they held their default. Hyprland's `hl.monitor()` seeds the rule from the existing one for that output, so an omitted key keeps its previous value: rotating a monitor and then setting it back to Normal left the display rotated until the next config reload, and the same applied to clearing a 10-bit or colour-management override. Live-apply callers now emit `transform, 0`, `bitdepth, 8` and `cm, srgb` explicitly. Verified against Hyprland 0.56.1.

  `mirror_of` and `vrr` are knowingly still affected: Hyprland clears a mirror with an empty value, which a comma-joined line can't carry unambiguously, and inheriting the global VRR setting needs a `-1` sentinel the compositor doesn't accept yet.

### Changed

- **`lines_from_monitors(explicit_hdr_defaults=...)` renamed to `for_live_apply=...`** — the flag now covers every field that needs an explicit default on live apply, not just the HDR SDR keys. Saved-config callers are unaffected; a config reload clears all monitor rules first, so omission still resets there.

## [0.8.0] - 2026-06-08

### Added

- **Resolution/position keywords** — new `mode` and `position` fields on `MonitorState` for Hyprland's special keywords (`preferred`, `highres`, `auto`, `auto-right`, …); parsed from saved config and emitted in place of explicit `WxH@RHz` / `XxY` values. Keyword-positioned monitors are excluded from `adjust_neighbors`, since the compositor places them. [#1](https://github.com/BlueManCZ/hyprland-monitors/pull/1)

## [0.7.0] - 2026-05-19

### Added

- **Luminance extras** — new `sdr_min_luminance`, `sdr_max_luminance`, `min_luminance`, `max_luminance`, and `max_avg_luminance` fields on `MonitorState`; parsed from and emitted to `monitor =` config lines using the matching Hyprland keys. `from_ipc` leaves all five at `None`: in HDR mode Hyprland substitutes the panel's EDID mastering luminance for `sdrMinLuminance`/`sdrMaxLuminance`, so IPC values cannot distinguish a user override from an EDID-derived default. They are populated only from saved config via `merge_saved_state`.
- **HDR mastering luminance from EDID** — `get_monitor_capabilities()` now also returns `max_luminance`, `max_avg_luminance`, and `min_luminance` (cd/m²) parsed from the panel's CTA-861 HDR Static Metadata Data Block. Each field is `None` when EDID omits the corresponding optional byte. Useful for picking safe per-panel defaults that don't over-drive OLEDs above their mastering luminance.

## [0.6.0] - 2026-05-17

### Added

- **SDR brightness/saturation extras** — new `sdr_brightness` and `sdr_saturation` fields on `MonitorState`; populated from IPC and emitted to `monitor =` config lines as `sdrbrightness`/`sdrsaturation`
- **`lines_from_monitors(explicit_hdr_defaults=...)`** — new keyword argument. When True, monitors with an HDR color-management preset emit explicit `sdrbrightness, 1` / `sdrsaturation, 1` even when the field is `None`, so callers applying lines live via `hl.monitor()` reset to defaults instead of inheriting the previous value

### Changed

- **`from_ipc` normalisation** — IPC's `"default"` color-management value (no preset active) now maps to `None`, matching the other config-line extras

## [0.5.0] - 2026-05-07

### Added

- **Description-based identification** — new `identify_by_description` field on `MonitorState`; when set, `lines_from_monitors` emits `monitor=desc:<description>` so saved configs follow the physical monitor across port changes
- **Identifier resolver** — new `resolve_identifier()` helper that maps a config-line identifier (port name or `desc:<prefix>`) to the matching `MonitorState`

### Changed

- **Config parsing** — `merge_saved_state` resolves both port-name and `desc:` forms, restoring the `identify_by_description` flag on a `desc:` match

## [0.4.0] - 2026-03-26

### Added

- **Mirror support** — new `mirror_of` field on `MonitorState` for monitor mirroring; parsed from and emitted to `monitor =` config lines (`mirror, <name>`)
- **Mirror validation** — new `validate_mirror()` helper rejects self-mirrors, chained mirrors, missing targets, and disabled targets

### Changed

- **Layout geometry** — mirrored monitors are excluded from `all_monitors_connected` and `adjust_neighbors`, preventing false gap detection and spurious position shifts
- **Dependency bump** — now requires `hyprland-socket` >= 0.9.0

## [0.3.0] - 2026-03-24

### Changed

- **DRM connector names** — fixed type name table to match kernel conventions (`DVI-I`, `DVI-D`, `DVI-A`, `DIN` instead of `DVII`, `DVID`, `DVIA`, `9PinDIN`)
- **Config output** — `color_management` is now always preserved from IPC state, ensuring the value is not silently dropped when at default
- **EDID parser** — magic numbers extracted into `_EDID_BLOCK_SIZE` and `_EDID_NUM_EXTENSIONS_BYTE` constants for clarity
- **Dependency bump** — now requires `hyprland-socket` >= 0.8.0

## [0.2.0] - 2026-03-22

### Changed

- **Stricter type annotations** — public APIs now use `Sequence` instead of `list` for read-only parameters and `Iterator` instead of `Generator` where appropriate
- **EDID parser** — `_read_edid_capabilities` now accepts `bytes | bytearray`
- **Config output** — `color_management` extra is omitted when at its default value, producing cleaner config lines

## [0.1.0] - 2026-03-19

### Added

- **Scale computation** — find all valid Hyprland scales for a given resolution, accounting for 1/120 quantization and integer-divisibility constraints
- **Layout geometry** — adjacency detection, connectivity validation (DFS), and automatic neighbor adjustment on resize/scale/transform changes
- **Config parsing** — bidirectional conversion between MonitorState objects and Hyprland `monitor =` config lines, including extras (bitdepth, vrr, color_management)
- **Hardware detection** — EDID parsing for HDR and 10-bit support, DRM kernel property queries for VRR capability

[0.9.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.9.0
[0.8.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.8.0
[0.7.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.7.0
[0.6.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.6.0
[0.5.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.5.0
[0.4.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.4.0
[0.3.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.3.0
[0.2.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.1.0
