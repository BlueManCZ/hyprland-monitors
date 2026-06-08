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
    "sdrbrightness": "sdr_brightness",
    "sdrsaturation": "sdr_saturation",
    "sdr_min_luminance": "sdr_min_luminance",
    "sdr_max_luminance": "sdr_max_luminance",
    "min_luminance": "min_luminance",
    "max_luminance": "max_luminance",
    "max_avg_luminance": "max_avg_luminance",
}

# Hyprland's default bit depth when no override is active.
_DEFAULT_BIT_DEPTH = 8

# Color-management presets that activate Hyprland's HDR pipeline. While one of
# these is active, omitting SDR/luminance keys from ``hl.monitor()`` leaves the
# previous value in place rather than resetting — see ``lines_from_monitors``'s
# ``explicit_hdr_defaults`` flag.
_HDR_CM_VALUES = frozenset({"hdr", "hdredid"})

# HDR-pipeline fields and the value Hyprland reports when the key is unset.
# Doubles as the IPC-default lookup in ``from_ipc``, so the values stay in
# sync across "is this an override?" and "what to emit for an explicit reset".
#
# ``sdr_min_luminance``/``sdr_max_luminance`` are intentionally omitted even
# though IPC exposes them: in HDR mode Hyprland substitutes the panel's EDID
# mastering luminance (so e.g. an unconfigured 603-nit OLED reports
# ``sdrMaxLuminance: 603`` rather than the non-HDR fallback of 80). There is
# no single value we can compare IPC against to tell "user override" from
# "EDID-derived default", so we can't safely round-trip them via IPC.
#
# ``min_luminance``/``max_luminance``/``max_avg_luminance`` are omitted for
# the same reason — Hyprland doesn't expose their defaults at all.
_HDR_FIELD_DEFAULTS: dict[str, float] = {
    "sdr_brightness": 1.0,
    "sdr_saturation": 1.0,
}

# Hyprland's special resolution/position keywords, accepted in place of an
# explicit "WxH@RHz" / "XxY" value.
# https://wiki.hypr.land/Configuring/Basics/Monitors/#general
_MODE_KEYWORDS = frozenset({"preferred", "highres", "highrr", "maxwidth"})

_POSITION_KEYWORDS = frozenset(
    {
        "auto",
        "auto-right",
        "auto-left",
        "auto-up",
        "auto-down",
        "auto-center-right",
        "auto-center-left",
        "auto-center-up",
        "auto-center-down",
    }
)


def _format_float_value(v: float) -> str:
    """Format a float as a config-line value (2dp, no trailing zeros).

    Keeps at least one integer digit so ``0`` renders as ``"0"`` rather than ``""``.
    """
    int_part, _, frac = f"{v:.2f}".partition(".")
    frac = frac.rstrip("0")
    return f"{int_part}.{frac}" if frac else int_part


def _ipc_float_extra(value: float, default: float) -> str | None:
    """Format an IPC float as a config-line override, or None when at default."""
    return _format_float_value(value) if value != default else None


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
    sdr_brightness: str | None = None
    sdr_saturation: str | None = None
    sdr_min_luminance: str | None = None
    sdr_max_luminance: str | None = None
    min_luminance: str | None = None
    max_luminance: str | None = None
    max_avg_luminance: str | None = None
    mirror_of: str | None = None
    # Hyprland keyword overrides ("preferred"/"auto"); None = use width/height/x/y.
    mode: str | None = None
    position: str | None = None
    disabled: bool = False
    description: str = ""
    identify_by_description: bool = False

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
            # IPC reports "default" when no preset is active — treat as no override.
            color_management=(
                m.color_management
                if m.color_management and m.color_management != "default"
                else None
            ),
            sdr_brightness=_ipc_float_extra(
                m.sdr_brightness, _HDR_FIELD_DEFAULTS["sdr_brightness"]
            ),
            sdr_saturation=_ipc_float_extra(
                m.sdr_saturation, _HDR_FIELD_DEFAULTS["sdr_saturation"]
            ),
            # sdr_min_luminance / sdr_max_luminance: IPC reports the live value
            # (EDID-derived in HDR mode), which we can't distinguish from a user
            # override. min/max/max_avg_luminance aren't exposed by IPC at all.
            # All five are populated only from saved config via merge_saved_state.
            mirror_of=m.mirror_of if m.mirror_of != "none" else None,
            mode=None,  # keyword overrides, restored from saved config by merge_saved_state
            position=None,
            disabled=m.disabled,
            description=m.description,
        )

    def update_geometry_from_ipc(self, m: Monitor) -> None:
        """Update geometry fields from IPC, preserving config-line extras (bit_depth, vrr, etc.)."""
        self.x = m.x
        self.y = m.y
        self.width = m.width
        self.height = m.height
        self.refresh_rate = m.refresh_rate
        self.scale = m.scale
        self.transform = m.transform
        self.mirror_of = m.mirror_of if m.mirror_of != "none" else None

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
    enabled = [m for m in monitors if not m.disabled and not m.mirror_of]
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
    # Keyword-positioned monitors (auto, …) are placed by the compositor — leave them.
    if mon.position:
        return
    new_w, new_h = mon.effective_size
    dw = new_w - old_w
    dh = new_h - old_h
    if dw == 0 and dh == 0:
        return
    for other in monitors:
        if other is mon or other.mirror_of or other.position:
            continue
        if dw != 0 and other.x >= mon.x + old_w:
            other.x += dw
        if dh != 0 and other.y >= mon.y + old_h:
            other.y += dh


def validate_mirror(
    monitors: Sequence[MonitorState], mon: MonitorState, target: str | None
) -> str | None:
    """Return an error message if the mirror target is invalid, or None if valid."""
    if target is None:
        return None
    if target == mon.name:
        return "A monitor cannot mirror itself"
    target_mon = next((m for m in monitors if m.name == target), None)
    if target_mon is None:
        return f"Mirror target '{target}' not found"
    if target_mon.mirror_of is not None:
        return f"Cannot mirror '{target}' — it is already mirroring '{target_mon.mirror_of}'"
    if target_mon.disabled:
        return f"Cannot mirror disabled monitor '{target}'"
    return None


def _identifier_for(mon: MonitorState) -> str:
    """Return the leading token used in the monitor= config line.

    When ``identify_by_description`` is set and a description is available,
    emits ``desc:<description>``. Hyprland matches this as a prefix against
    the monitor's IPC description, so the connector port can change without
    breaking the rule. Falls back to the connector name otherwise.

    The description is truncated at the first comma so the line remains
    splittable on commas — Hyprland's prefix match still succeeds on the
    truncated string.
    """
    if mon.identify_by_description and mon.description:
        prefix = mon.description.split(",", 1)[0].strip()
        if prefix:
            return f"desc:{prefix}"
    return mon.name


def lines_from_monitors(
    monitors: Sequence[MonitorState], *, explicit_hdr_defaults: bool = False
) -> list[str]:
    """Build monitor config lines from Monitor objects.

    When ``explicit_hdr_defaults`` is True, monitors with an HDR color-management
    preset emit explicit ``sdrbrightness, 1`` / ``sdrsaturation, 1`` even when
    those fields are ``None``. Hyprland's live ``hl.monitor()`` API is additive —
    omitting these keys leaves the previous value in place rather than resetting
    to 1.0 — so callers that apply the lines live (rather than write them to a
    config file) should pass ``True`` to get reset-on-default semantics. Saved
    config files normally leave it ``False`` to stay free of redundant defaults.
    The luminance keys are intentionally excluded — see ``_HDR_FIELD_DEFAULTS``.
    """
    lines = []
    for mon in monitors:
        ident = _identifier_for(mon)
        if mon.disabled:
            lines.append(f"{ident}, disable")
            continue
        # A keyword override (mode/position) wins over the explicit values.
        res = mon.mode or f"{mon.width}x{mon.height}@{mon.refresh_rate:.2f}Hz"
        pos = mon.position or f"{mon.x}x{mon.y}"
        scale_str = _format_scale(mon.scale)
        parts = [ident, res, pos, scale_str]
        if mon.transform:
            parts.extend(["transform", str(mon.transform)])
        is_hdr = mon.color_management in _HDR_CM_VALUES
        for config_key, field in _CONFIG_TO_FIELD.items():
            val = getattr(mon, field)
            if val is None and explicit_hdr_defaults and is_hdr and field in _HDR_FIELD_DEFAULTS:
                val = _format_float_value(_HDR_FIELD_DEFAULTS[field])
            if val is not None:
                parts.extend([config_key, val])
        if mon.mirror_of is not None:
            parts.extend(["mirror", mon.mirror_of])
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
        if key == "mirror":
            extras["mirror_of"] = tail[i + 1]
        else:
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


def resolve_identifier(token: str, monitors: Sequence[MonitorState]) -> MonitorState | None:
    """Resolve a config-line identifier to a connected monitor.

    The identifier is either a connector name (``DP-1``) or a description
    prefix (``desc:Some Monitor``). Description matching follows Hyprland's
    rule: prefix match against the monitor's description string. The first
    matching monitor wins.
    """
    if token.startswith("desc:"):
        prefix = token.removeprefix("desc:").strip()
        if not prefix:
            return None
        for mon in monitors:
            if mon.description.startswith(prefix):
                return mon
        return None
    for mon in monitors:
        if mon.name == token:
            return mon
    return None


def merge_saved_state(monitors: Sequence[MonitorState], saved_lines: list[str]) -> None:
    """Merge saved config state (extras, disabled flag, identifier mode) into monitors.

    Saved config wins for values IPC can't distinguish (e.g. vrr=2 vs vrr=1
    both show as vrr:true in IPC). Also restores ``disabled`` and sets
    ``identify_by_description`` for monitors saved with a ``desc:`` token.

    Resolution/position are recorded only when they hold a Hyprland keyword
    (``preferred``, ``auto``); literal values defer to IPC, the live geometry source.
    """
    for raw_line in saved_lines:
        parts = _split_config_line(raw_line)
        if not parts or not parts[0]:
            continue
        token = parts[0]
        mon = resolve_identifier(token, monitors)
        if mon is None:
            continue
        if token.startswith("desc:"):
            mon.identify_by_description = True
        if len(parts) >= 2 and parts[1].lower() == "disable":
            mon.disabled = True
            continue
        if len(parts) >= 4:
            mode_val = parts[1].lower()
            mon.mode = mode_val if mode_val in _MODE_KEYWORDS else None
            pos_val = parts[2].lower()
            mon.position = pos_val if pos_val in _POSITION_KEYWORDS else None
            for field, value in _parse_extras_from_parts(parts).items():
                setattr(mon, field, value)


def parse_mode(mode_str: str) -> ParsedMode:
    """Parse a mode string like '1920x1080@60.00Hz' into Monitor field names."""
    mode_str = mode_str.replace(" Hz", "Hz")
    res_part, hz_part = mode_str.split("@")
    w_str, h_str = res_part.split("x")
    rate = float(hz_part.removesuffix("Hz"))
    return {"width": int(w_str), "height": int(h_str), "refresh_rate": rate}
