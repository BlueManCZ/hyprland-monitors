# hyprland-monitors

Monitor management utilities for [Hyprland](https://hyprland.org/).
Built on top of [hyprland-socket](https://github.com/BlueManCZ/hyprland-socket).

Scale computation, layout geometry, config line parsing, and hardware capability
detection for Hyprland monitor management.

## Installation

```bash
pip install hyprland-monitors
```

## Usage

### Scale computation

Hyprland quantizes display scale to multiples of 1/120 and requires both
dimensions to divide evenly. `compute_valid_scales` finds all valid scales
for a given resolution.

```python
from hyprland_monitors.monitors import compute_valid_scales, nearest_scale_index

scales = compute_valid_scales(3440, 1440)
for s in scales:
    print(f"  {s.label} ({s.value})")

# Find the closest valid scale to a target
idx = nearest_scale_index(scales, 1.5)
print(f"Nearest to 1.5: {scales[idx].label}")
```

### Layout geometry

Check adjacency, detect gaps, and adjust positions when monitors resize.

```python
from hyprland_monitors.monitors import (
    MonitorState, is_adjacent, all_monitors_connected, adjust_neighbors,
)

a = MonitorState(name="DP-1", make="", model="", width=1920, height=1080,
                 refresh_rate=60.0, x=0, y=0, scale=1.0)
b = MonitorState(name="DP-2", make="", model="", width=1920, height=1080,
                 refresh_rate=60.0, x=1920, y=0, scale=1.0)

print(is_adjacent(a, b))                # True — edge-adjacent
print(all_monitors_connected([a, b]))   # True — connected group

# After resizing a, shift b to maintain adjacency
old_w, old_h = a.effective_size
a.scale = 2.0
adjust_neighbors([a, b], a, old_w, old_h)
print(b.x)  # 960 — shifted to stay adjacent
```

### Config line parsing

Parse and generate Hyprland `monitor =` config lines.

```python
from hyprland_monitors.monitors import lines_from_monitors, parse_mode, parse_extras

lines = lines_from_monitors([a, b])
# ['DP-1, 1920x1080@60.00Hz, 0x0, 2', 'DP-2, 1920x1080@60.00Hz, 960x0, 1']

mode = parse_mode("3440x1440@165.00Hz")
# {'width': 3440, 'height': 1440, 'refresh_rate': 165.0}

extras = parse_extras("DP-1, 1920x1080@60, 0x0, 1, bitdepth, 10, vrr, 1")
# {'bit_depth': '10', 'vrr': '1'}
```

### Hardware detection

Query monitor capabilities via EDID parsing and DRM kernel properties.

```python
from hyprland_monitors.hardware import get_monitor_capabilities

caps = get_monitor_capabilities("DP-1")
# {'hdr': True, 'ten_bit': True, 'vrr': False}
```

## License

MIT
