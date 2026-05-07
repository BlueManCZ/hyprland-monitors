# Changelog

All notable changes to hyprland-monitors will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.5.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.5.0
[0.4.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.4.0
[0.3.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.3.0
[0.2.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprland-monitors/releases/tag/v0.1.0
