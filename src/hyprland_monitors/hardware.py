"""Hardware capability queries — DRM/EDID parsing and sysfs inspection.

These queries run against the kernel (ioctl, sysfs) rather than Hyprland's IPC.
"""

import ctypes
import fcntl
import functools
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, TypedDict

_DRM_DIR = Path("/sys/class/drm")

_EDID_BLOCK_SIZE = 128
_EDID_NUM_EXTENSIONS_BYTE = 126


class MonitorCapabilities(TypedDict):
    """Display capabilities read from EDID and DRM properties."""

    hdr: bool
    """True if the display supports HDR Static Metadata."""
    ten_bit: bool
    """True if the display supports 10-bit (or higher) color depth."""
    vrr: bool
    """True if the display supports variable refresh rate."""
    max_luminance: float | None
    """Desired content max luminance in cd/m², from the EDID HDR Static Metadata block.

    None when EDID does not include the optional max luminance byte.
    """
    max_avg_luminance: float | None
    """Desired content max frame-average luminance in cd/m², from EDID.

    None when EDID does not include the optional max-frame-average byte.
    """
    min_luminance: float | None
    """Desired content min luminance in cd/m², from EDID.

    None when EDID lacks either the min byte or the max byte it is derived from.
    """


class _EdidCaps(NamedTuple):
    """Parsed EDID capabilities (internal)."""

    ten_bit: bool
    hdr: bool
    max_luminance: float | None = None
    max_avg_luminance: float | None = None
    min_luminance: float | None = None


# ---------------------------------------------------------------------------
# sysfs helpers
# ---------------------------------------------------------------------------


@functools.cache
def _find_primary_card() -> str | None:
    """Find the primary GPU card name (e.g. 'card0') via boot_vga."""
    try:
        cards = sorted(
            e for e in _DRM_DIR.iterdir() if e.name.startswith("card") and "-" not in e.name
        )
    except OSError:
        return None
    for entry in cards:
        try:
            if (entry / "device" / "boot_vga").read_text().strip() == "1":
                return entry.name
        except OSError:
            continue
    # No boot_vga found — fall back to the first available card
    return cards[0].name if cards else None


def list_connectors() -> dict[str, bool]:
    """Return DRM connectors on the primary GPU and their connection status.

    Only scans the primary GPU (boot_vga=1) to avoid name collisions on
    multi-GPU systems. Returns a dict mapping connector names
    (e.g. 'DP-1', 'HDMI-A-1') to whether a display is currently connected.
    """
    card = _find_primary_card()
    if card is None:
        return {}
    prefix = card + "-"
    result: dict[str, bool] = {}
    try:
        for entry in sorted(_DRM_DIR.iterdir()):
            if not entry.name.startswith(prefix):
                continue
            name = entry.name.removeprefix(prefix)
            try:
                status = (entry / "status").read_text().strip()
                result[name] = status == "connected"
            except OSError:
                continue
    except OSError:
        pass
    return result


def _find_drm_connector(name: str) -> tuple[str, Path] | None:
    """Find /sys/class/drm/card<N>-<name> for a connected connector on the primary GPU.

    Returns (card_name, connector_path) or None.
    """
    card = _find_primary_card()
    if card is None:
        return None
    entry = _DRM_DIR / f"{card}-{name}"
    try:
        if entry.is_dir() and (entry / "status").read_text().strip() == "connected":
            return card, entry
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# EDID parsing
# ---------------------------------------------------------------------------


def _decode_hdr_max_luminance(code: int) -> float:
    """Decode a CTA-861 HDR Static Metadata max-luminance byte to cd/m²."""
    return 50.0 * 2.0 ** (code / 32.0)


def _parse_hdr_static_metadata(
    payload: bytes | bytearray,
) -> tuple[float | None, float | None, float | None]:
    """Parse an HDR Static Metadata Data Block payload (CTA-861-H §7.5.13).

    *payload* starts with the extended tag code (0x06) and is up to six bytes:
    extended tag, EOTF flags, static metadata flags, then optional Desired
    Content Max Luminance, Max Frame-Average Luminance, and Min Luminance code
    bytes. The optional luminance bytes are ordered max → max-avg → min and each
    requires the preceding one; older displays may advertise HDR support (the
    block exists) without any of them. Returns ``(max, max_avg, min)`` cd/m²
    values, with ``None`` for any field the EDID omits.
    """
    if len(payload) < 4:
        return None, None, None
    max_lum = _decode_hdr_max_luminance(payload[3])
    max_avg_lum = _decode_hdr_max_luminance(payload[4]) if len(payload) >= 5 else None
    min_lum = max_lum * (payload[5] / 255.0) ** 2 / 100.0 if len(payload) >= 6 else None
    return max_lum, max_avg_lum, min_lum


def _read_edid_capabilities(edid_data: bytes | bytearray) -> _EdidCaps:
    """Parse EDID bytes for 10-bit, HDR, and HDR mastering luminance."""
    if len(edid_data) < _EDID_BLOCK_SIZE:
        return _EdidCaps(ten_bit=False, hdr=False)

    # Bit depth from base EDID byte 20 (bits 6-4), requires EDID >= 1.4
    ten_bit = False
    version = (edid_data[18], edid_data[19])
    if version >= (1, 4):
        depth_code = (edid_data[20] >> 4) & 0x07
        # 0=undef, 1=6bpc, 2=8bpc, 3=10bpc, 4=12bpc, 5=14bpc, 6=16bpc
        ten_bit = depth_code >= 3

    # Search CEA-861 extension blocks for HDR Static Metadata (extended tag 6)
    num_ext = edid_data[_EDID_NUM_EXTENSIONS_BYTE]
    for ext_idx in range(num_ext):
        offset = _EDID_BLOCK_SIZE * (ext_idx + 1)
        if offset + _EDID_BLOCK_SIZE > len(edid_data):
            break
        ext = edid_data[offset : offset + _EDID_BLOCK_SIZE]
        if ext[0] != 0x02:  # Not a CEA extension
            continue
        dtd_start = min(ext[2], len(ext))
        pos = 4
        while pos < dtd_start - 1:
            header = ext[pos]
            tag = (header >> 5) & 0x07
            length = header & 0x1F
            if tag == 7 and length >= 1:  # Extended tag block
                ext_tag = ext[pos + 1]
                if ext_tag == 6:  # HDR Static Metadata Data Block
                    payload = ext[pos + 1 : pos + 1 + length]
                    max_lum, max_avg_lum, min_lum = _parse_hdr_static_metadata(payload)
                    return _EdidCaps(
                        ten_bit=ten_bit,
                        hdr=True,
                        max_luminance=max_lum,
                        max_avg_luminance=max_avg_lum,
                        min_luminance=min_lum,
                    )
            pos += length + 1

    return _EdidCaps(ten_bit=ten_bit, hdr=False)


# ---------------------------------------------------------------------------
# DRM ioctl structures and constants
# ---------------------------------------------------------------------------


def _IOWR(type_: int, nr: int, size: int) -> int:
    """Construct a read/write ioctl request number (equivalent to Linux _IOWR macro)."""
    return (3 << 30) | (size << 16) | (type_ << 8) | nr


_DRM_IOCTL_BASE = ord("d")


class _DrmModeCardRes(ctypes.Structure):
    """drm_mode_card_res — returned by DRM_IOCTL_MODE_GETRESOURCES."""

    _fields_ = [
        ("fb_id_ptr", ctypes.c_uint64),
        ("crtc_id_ptr", ctypes.c_uint64),
        ("connector_id_ptr", ctypes.c_uint64),
        ("encoder_id_ptr", ctypes.c_uint64),
        ("count_fbs", ctypes.c_uint32),
        ("count_crtcs", ctypes.c_uint32),
        ("count_connectors", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class _DrmModeGetConnector(ctypes.Structure):
    """drm_mode_get_connector — returned by DRM_IOCTL_MODE_GETCONNECTOR."""

    _fields_ = [
        ("encoders_ptr", ctypes.c_uint64),
        ("modes_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_modes", ctypes.c_uint32),
        ("count_props", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32),
        ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32),
        ("connection", ctypes.c_uint32),
        ("mm_width", ctypes.c_uint32),
        ("mm_height", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32),
    ]


class _DrmModeGetProperty(ctypes.Structure):
    """drm_mode_get_property — returned by DRM_IOCTL_MODE_GETPROPERTY."""

    _fields_ = [
        ("values_ptr", ctypes.c_uint64),
        ("enum_blob_ptr", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("count_values", ctypes.c_uint32),
        ("count_enum_blobs", ctypes.c_uint32),
    ]


_DRM_MODE_MODEINFO_SIZE = 68  # sizeof(struct drm_mode_modeinfo)

_DRM_GETRESOURCES = _IOWR(_DRM_IOCTL_BASE, 0xA0, ctypes.sizeof(_DrmModeCardRes))
_DRM_GETCONNECTOR = _IOWR(_DRM_IOCTL_BASE, 0xA7, ctypes.sizeof(_DrmModeGetConnector))
_DRM_GETPROPERTY = _IOWR(_DRM_IOCTL_BASE, 0xAA, ctypes.sizeof(_DrmModeGetProperty))

# Connector type codes from the kernel's drm_connector_enum_list.
_DRM_CONNECTED = 1  # drm_connector_status: connector has a display attached

_DRM_CONNECTOR_TYPES: tuple[str, ...] = (
    "Unknown",
    "VGA",
    "DVI-I",
    "DVI-D",
    "DVI-A",
    "Composite",
    "SVIDEO",
    "LVDS",
    "Component",
    "DIN",
    "DP",
    "HDMI-A",
    "HDMI-B",
    "TV",
    "eDP",
    "Virtual",
    "DSI",
    "DPI",
    "Writeback",
    "SPI",
    "USB",
)


# ---------------------------------------------------------------------------
# DRM ioctl helpers
# ---------------------------------------------------------------------------


def _get_connector_ids(fd: int) -> list[int]:
    """Return the list of DRM connector IDs via GETRESOURCES ioctl."""
    res = _DrmModeCardRes()
    fcntl.ioctl(fd, _DRM_GETRESOURCES, res)
    if res.count_connectors == 0:
        return []

    connector_ids = (ctypes.c_uint32 * res.count_connectors)()
    res2 = _DrmModeCardRes()
    res2.connector_id_ptr = ctypes.addressof(connector_ids)
    res2.count_connectors = res.count_connectors
    fcntl.ioctl(fd, _DRM_GETRESOURCES, res2)
    return list(connector_ids)


def _check_connector_vrr(fd: int, conn: _DrmModeGetConnector) -> bool:
    """Check whether a matched connector has vrr_capable=1."""
    if conn.count_props == 0:
        return False

    props = (ctypes.c_uint32 * conn.count_props)()
    prop_vals = (ctypes.c_uint64 * conn.count_props)()
    # Must provide buffers for modes/encoders even though we only need properties.
    modes_buf = ctypes.create_string_buffer(_DRM_MODE_MODEINFO_SIZE * max(conn.count_modes, 1))
    enc_buf = (ctypes.c_uint32 * max(conn.count_encoders, 1))()

    conn2 = _DrmModeGetConnector()
    conn2.encoders_ptr = ctypes.addressof(enc_buf)
    conn2.modes_ptr = ctypes.addressof(modes_buf)
    conn2.props_ptr = ctypes.addressof(props)
    conn2.prop_values_ptr = ctypes.addressof(prop_vals)
    conn2.count_modes = conn.count_modes
    conn2.count_props = conn.count_props
    conn2.count_encoders = conn.count_encoders
    conn2.connector_id = conn.connector_id
    fcntl.ioctl(fd, _DRM_GETCONNECTOR, conn2)

    for j in range(conn.count_props):
        prop = _DrmModeGetProperty()
        prop.prop_id = props[j]
        fcntl.ioctl(fd, _DRM_GETPROPERTY, prop)
        if prop.name == b"vrr_capable":
            return prop_vals[j] == 1
    return False


@contextmanager
def _open_drm(card: str) -> Iterator[int]:
    """Open a DRM device node and ensure it is closed on exit."""
    fd = os.open(f"/dev/dri/{card}", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


def _read_drm_vrr_capable(name: str, card: str) -> bool:
    """Read the kernel's vrr_capable DRM connector property.

    This is vendor-agnostic — the kernel sets it for AMD FreeSync,
    VESA Adaptive-Sync, HDMI 2.1 VRR, etc.
    """
    try:
        with _open_drm(card) as fd:
            for conn_id in _get_connector_ids(fd):
                conn = _DrmModeGetConnector()
                conn.connector_id = conn_id
                fcntl.ioctl(fd, _DRM_GETCONNECTOR, conn)

                ct = conn.connector_type
                type_name = _DRM_CONNECTOR_TYPES[ct] if ct < len(_DRM_CONNECTOR_TYPES) else str(ct)
                drm_name = f"{type_name}-{conn.connector_type_id}"
                if drm_name != name or conn.connection != _DRM_CONNECTED:
                    continue

                return _check_connector_vrr(fd, conn)
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_monitor_capabilities(name: str) -> MonitorCapabilities | None:
    """Read display capabilities for a monitor.

    Uses EDID parsing (10-bit, HDR) and kernel DRM properties (VRR).
    Returns MonitorCapabilities or None if the connector is not found.
    """
    result = _find_drm_connector(name)
    if result is None:
        return None
    card, connector_dir = result

    try:
        edid_caps = _read_edid_capabilities((connector_dir / "edid").read_bytes())
    except OSError:
        edid_caps = _EdidCaps(ten_bit=False, hdr=False)

    return MonitorCapabilities(
        ten_bit=edid_caps.ten_bit,
        hdr=edid_caps.hdr,
        vrr=_read_drm_vrr_capable(name, card),
        max_luminance=edid_caps.max_luminance,
        max_avg_luminance=edid_caps.max_avg_luminance,
        min_luminance=edid_caps.min_luminance,
    )
