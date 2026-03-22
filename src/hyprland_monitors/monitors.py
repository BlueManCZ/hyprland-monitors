"""Monitor state and pure helper functions for monitor management."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Self, TypedDict

from hyprland_socket import Monitor


class ScaleOption(NamedTuple):
    """A valid Hyprland scale: precise 1/120-multiple value and its display label."""

    value: float
    label: str


class ParsedMode(TypedDict):
    width: int
    height: int
    refresh_rate: float


TRANSFORMS = {
    0: "Normal",
    1: "90\u00b0",
    2: "180\u00b0",
    3: "270\u00b0",
    4: "Flipped",
    5: "Flipped 90\u00b0",
    6: "Flipped 180\u00b0",
    7: "Flipped 270\u00b0",
}

# Transforms where width and height swap (90°/270° and their flipped variants).
_SWAPPED_TRANSFORMS = frozenset({1, 3, 5, 7})

# Config key -> MonitorState field name for the extras that live in config lines.
# Order determines output order in lines_from_monitors.
_CONFIG_TO_FIELD: dict[str, str] = {
    "bitdepth": "bit_depth",
    "vrr": "vrr",
    "cm": "color_management",
}

# Hyprland's default bit depth when no override is active.
_DEFAULT_BIT_DEPTH = 8


def _format_scale(value: float) -> str:
    """Format a scale value as Hyprland displays it (2dp, no trailing zeros)."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass(slots=True)
class MonitorState:
    """Mutable working copy of a hyprland_socket.Monitor."""

    name: str
    make: str
    model: str
    width: int
    height: int
    refresh_rate: float
    x: int
    y: int
    scale: float
    transform: int = 0
    focused: bool = False
    available_modes: tuple[str, ...] = ()
    bit_depth: str | None = None
    vrr: str | None = None
    color_management: str | None = None
    disabled: bool = False

    @classmethod
    def from_ipc(cls, m: Monitor) -> Self:
        """Create from a hyprland_socket.Monitor, discarding IPC vrr (unreliable)."""
        return cls(
            name=m.name,
            make=m.make,
            model=m.model,
            width=m.width,
            height=m.height,
            refresh_rate=m.refresh_rate,
            x=m.x,
            y=m.y,
            scale=m.scale,
            transform=m.transform,
            focused=m.focused,
            available_modes=tuple(m.available_modes),
            # Extras are None when at their default — only non-default overrides
            # get written to config lines by lines_from_monitors.
            bit_depth=str(m.bit_depth) if m.bit_depth != _DEFAULT_BIT_DEPTH else None,
            vrr=None,  # IPC returns bool; saved config is authoritative
            color_management=m.color_management if m.color_management != "default" else None,
            disabled=m.disabled,
        )

    def update_geometry_from_ipc(self, m: Monitor) -> None:
        """Update geometry fields from IPC, preserving extras (vrr, bit_depth, color_management)."""
        self.x = m.x
        self.y = m.y
        self.width = m.width
        self.height = m.height
        self.refresh_rate = m.refresh_rate
        self.scale = m.scale
        self.transform = m.transform

    @property
    def effective_size(self) -> tuple[int, int]:
        """Return (w, h) accounting for rotation and scale."""
        w, h = self.width, self.height
        if self.transform in _SWAPPED_TRANSFORMS:
            w, h = h, w
        w = round(w / self.scale)
        h = round(h / self.scale)
        return w, h


def compute_valid_scales(
    w: int, h: int, lo: float = 0.5, hi: float = 4.0, min_effective: int = 512
) -> list[ScaleOption]:
    """Return a sorted list of ScaleOption(value, label) that Hyprland accepts.

    Hyprland quantizes scale to multiples of 1/120, then checks that both
    width/scale and height/scale are exact integers. It reports scales rounded
    to 2dp, but uses full precision internally. We store both: the full-precision
    value (for IPC and position calculations) and the 2dp label (for display).

    Scales that would make either effective dimension smaller than min_effective
    are excluded to prevent unusably small resolutions.
    """
    seen_labels: set[str] = set()
    scales: list[ScaleOption] = []
    min_tick = round(lo * 120)
    max_tick = round(hi * 120)
    w120 = w * 120
    h120 = h * 120
    for tick in range(max(1, min_tick), max_tick + 1):
        if w120 % tick != 0 or h120 % tick != 0:
            continue
        if w120 // tick < min_effective or h120 // tick < min_effective:
            break
        s = tick / 120.0
        label = _format_scale(s)
        if label not in seen_labels:
            seen_labels.add(label)
            scales.append(ScaleOption(s, label))
    return scales


def nearest_scale_index(scales: Sequence[ScaleOption], target: float) -> int:
    """Return the index of the scale closest to *target*.

    *scales* must be non-empty; raises ``ValueError`` otherwise.
    """
    if not scales:
        raise ValueError("scales must not be empty")
    return min(range(len(scales)), key=lambda i: abs(scales[i].value - target))


def is_adjacent(a: MonitorState, b: MonitorState) -> bool:
    """Check if two monitors share at least one pixel of edge (adjacent, no gap)."""
    aw, ah = a.effective_size
    bw, bh = b.effective_size
    x_overlap = a.x < b.x + bw and a.x + aw > b.x
    y_overlap = a.y < b.y + bh and a.y + ah > b.y
    h_touch = (a.x + aw == b.x or b.x + bw == a.x) and y_overlap
    v_touch = (a.y + ah == b.y or b.y + bh == a.y) and x_overlap
    return h_touch or v_touch


def all_monitors_connected(monitors: Sequence[MonitorState]) -> bool:
    """Check if all enabled monitors form a connected group.

    Returns True if there are 0 or 1 enabled monitors.
    """
    enabled = [m for m in monitors if not m.disabled]
    if len(enabled) <= 1:
        return True

    # Connectivity check (DFS)
    visited = {0}
    stack = [0]
    while stack:
        idx = stack.pop()
        for j, other in enumerate(enabled):
            if j not in visited and is_adjacent(enabled[idx], other):
                visited.add(j)
                stack.append(j)
    return len(visited) == len(enabled)


def adjust_neighbors(
    monitors: Sequence[MonitorState], mon: MonitorState, old_w: int, old_h: int
) -> None:
    """Shift monitors to maintain adjacency after a resize.

    old_w/old_h are the effective size before the change.
    mon must already contain the new values.
    """
    new_w, new_h = mon.effective_size
    dw = new_w - old_w
    dh = new_h - old_h
    if dw == 0 and dh == 0:
        return
    for other in monitors:
        if other is mon:
            continue
        if dw != 0 and other.x >= mon.x + old_w:
            other.x += dw
        if dh != 0 and other.y >= mon.y + old_h:
            other.y += dh


def lines_from_monitors(monitors: Sequence[MonitorState]) -> list[str]:
    """Build monitor config lines from Monitor objects."""
    lines = []
    for mon in monitors:
        if mon.disabled:
            lines.append(f"{mon.name}, disable")
            continue
        res = f"{mon.width}x{mon.height}@{mon.refresh_rate:.2f}Hz"
        pos = f"{mon.x}x{mon.y}"
        scale_str = _format_scale(mon.scale)
        parts = [mon.name, res, pos, scale_str]
        if mon.transform:
            parts.extend(["transform", str(mon.transform)])
        for config_key, field in _CONFIG_TO_FIELD.items():
            val = getattr(mon, field)
            if val is not None:
                parts.extend([config_key, val])
        lines.append(", ".join(parts))
    return lines


def _split_config_line(raw_line: str) -> list[str]:
    """Strip optional 'monitor =' prefix and split into comma-separated parts."""
    cleaned = raw_line.removeprefix("monitor").strip().removeprefix("=").strip()
    return [p.strip() for p in cleaned.split(",")]


def _parse_extras_from_parts(parts: list[str]) -> dict[str, str]:
    """Parse extra key-value options from already-split config parts.

    First 4 parts are: name, resolution, position, scale.
    After that, pairs of key, value.
    """
    extras: dict[str, str] = {}
    tail = parts[4:]
    i = 0
    while i + 1 < len(tail):
        key = tail[i].lower()
        mapped = _CONFIG_TO_FIELD.get(key)
        if mapped:
            extras[mapped] = tail[i + 1]
        # Unmapped keys (e.g. "transform") are ignored — handled natively.
        i += 2
    return extras


def parse_extras(line: str) -> dict[str, str]:
    """Parse extra key-value options from a monitor config line.

    Given a line like 'DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10, cm, srgb',
    returns {'bit_depth': '10', 'color_management': 'srgb'}.
    """
    return _parse_extras_from_parts(_split_config_line(line))


def merge_saved_state(monitors: Sequence[MonitorState], saved_lines: list[str]) -> None:
    """Merge saved config state (extras and disabled flag) into Monitor objects.

    Saved config wins for values IPC can't distinguish
    (e.g. vrr=2 vs vrr=1 both show as vrr:true in IPC).
    Also restores disabled state from saved config.
    """
    saved_extras = {}
    disabled_names: set[str] = set()
    for raw_line in saved_lines:
        parts = _split_config_line(raw_line)
        if len(parts) >= 2 and parts[1].lower() == "disable":
            disabled_names.add(parts[0])
            continue
        if len(parts) >= 4:
            saved_extras[parts[0]] = _parse_extras_from_parts(parts)
    for mon in monitors:
        if mon.name in disabled_names:
            mon.disabled = True
        extras = saved_extras.get(mon.name)
        if extras:
            for field, value in extras.items():
                setattr(mon, field, value)


def parse_mode(mode_str: str) -> ParsedMode:
    """Parse a mode string like '1920x1080@60.00Hz' into Monitor field names."""
    mode_str = mode_str.replace(" Hz", "Hz")
    res_part, hz_part = mode_str.split("@")
    w_str, h_str = res_part.split("x")
    rate = float(hz_part.removesuffix("Hz"))
    return {"width": int(w_str), "height": int(h_str), "refresh_rate": rate}
