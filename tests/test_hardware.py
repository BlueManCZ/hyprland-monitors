"""Tests for EDID capability parsing and DRM VRR detection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hyprland_monitors import get_monitor_capabilities, list_connectors
from hyprland_monitors.hardware import (
    _EdidCaps,
    _find_primary_card,
    _read_drm_vrr_capable,
    _read_edid_capabilities,
)


@pytest.fixture
def clear_card_cache():
    """Clear _find_primary_card cache so patched _DRM_DIR takes effect."""
    _find_primary_card.cache_clear()
    yield
    _find_primary_card.cache_clear()


def _make_base_edid(version=(1, 4), depth_code=0, num_extensions=0):
    """Build a minimal 128-byte base EDID block.

    depth_code: bits 6-4 of byte 20 (0=undef, 1=6bpc, 2=8bpc, 3=10bpc, etc.)
    """
    data = bytearray(128)
    data[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    data[18] = version[0]
    data[19] = version[1]
    data[20] = 0x80 | ((depth_code & 0x07) << 4)
    data[126] = num_extensions
    return data


def _make_cea_extension(
    has_hdr: bool = False,
    *,
    max_lum_code: int | None = None,
    max_avg_lum_code: int | None = None,
    min_lum_code: int | None = None,
) -> bytearray:
    """Build a 128-byte CEA-861 extension block.

    When ``has_hdr`` is set, embeds an HDR Static Metadata Data Block. Pass any
    of the ``*_code`` parameters to include the corresponding optional luminance
    byte; later bytes are only included when the earlier ones are present (per
    spec).
    """
    ext = bytearray(128)
    ext[0] = 0x02  # CEA extension tag
    ext[1] = 0x03  # Revision 3

    pos = 4
    if has_hdr:
        # Required: extended tag (0x06), EOTF flags, static metadata flags.
        block_bytes = [0x06, 0x01, 0x00]
        for code in (max_lum_code, max_avg_lum_code, min_lum_code):
            if code is None:
                break
            block_bytes.append(code & 0xFF)
        length = len(block_bytes)
        ext[pos] = (7 << 5) | length
        for i, b in enumerate(block_bytes):
            ext[pos + 1 + i] = b
        pos += 1 + length

    ext[2] = pos  # DTD start offset
    return ext


def _build_fake_drm(tmp_path: Path, connectors: dict[str, str]) -> Path:
    """Build a fake /sys/class/drm tree.

    connectors: mapping of connector suffix (e.g. 'DP-1') to status string.
    Returns the fake DRM directory path.
    """
    drm = tmp_path / "drm"
    card_dir = drm / "card0"
    card_dir.mkdir(parents=True)
    boot_vga = card_dir / "device" / "boot_vga"
    boot_vga.parent.mkdir()
    boot_vga.write_text("1\n")

    for name, status in connectors.items():
        conn_dir = drm / f"card0-{name}"
        conn_dir.mkdir()
        (conn_dir / "status").write_text(f"{status}\n")

    return drm


class TestEdidParsing:
    def test_10bit_detected(self):
        edid = _make_base_edid(depth_code=3)
        caps = _read_edid_capabilities(edid)
        assert caps.ten_bit is True
        assert caps.hdr is False

    def test_12bit_detected(self):
        edid = _make_base_edid(depth_code=4)
        caps = _read_edid_capabilities(edid)
        assert caps.ten_bit is True

    def test_8bit_not_flagged(self):
        edid = _make_base_edid(depth_code=2)
        caps = _read_edid_capabilities(edid)
        assert caps.ten_bit is False

    def test_6bit_not_flagged(self):
        edid = _make_base_edid(depth_code=1)
        caps = _read_edid_capabilities(edid)
        assert caps.ten_bit is False

    def test_hdr_detected(self):
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(has_hdr=True)
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.hdr is True
        assert caps.ten_bit is True

    def test_no_hdr_in_cea(self):
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(has_hdr=False)
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.hdr is False

    def test_hdr_block_without_luminance_bytes(self):
        # Minimal HDR block: just EOTF + static metadata flags, no luminance.
        # The block is valid HDR signalling; luminance fields stay None.
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(has_hdr=True)
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.hdr is True
        assert caps.max_luminance is None
        assert caps.max_avg_luminance is None
        assert caps.min_luminance is None

    def test_hdr_block_decodes_max_luminance(self):
        # code 115 → 50 * 2^(115/32) ≈ 603.65 cd/m² (the LG OLED ULTRAGEAR+ panel).
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(has_hdr=True, max_lum_code=115)
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.hdr is True
        assert caps.max_luminance is not None
        assert caps.max_luminance == pytest.approx(603.65, abs=0.5)
        # Optional bytes that weren't included stay None.
        assert caps.max_avg_luminance is None
        assert caps.min_luminance is None

    def test_hdr_block_decodes_all_three_luminance_values(self):
        # 603 cd/m² peak, 400 cd/m² average, 0.03 cd/m² minimum
        # (matching the drm_info reading from the LG OLED ULTRAGEAR+).
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(
            has_hdr=True,
            max_lum_code=115,
            max_avg_lum_code=96,  # 50 * 2^(96/32) = 400 cd/m²
            min_lum_code=18,  # 603 * (18/255)^2 / 100 ≈ 0.03 cd/m²
        )
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.max_luminance == pytest.approx(603.65, abs=0.5)
        assert caps.max_avg_luminance == pytest.approx(400.0, abs=1.0)
        assert caps.min_luminance == pytest.approx(0.030, abs=0.001)

    def test_hdr_block_decodes_295_nit_panel(self):
        # Second monitor from naveline67's setup: 295 cd/m² peak, 0.298 cd/m² min.
        base = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(
            has_hdr=True,
            max_lum_code=82,
            max_avg_lum_code=82,
            min_lum_code=81,
        )
        caps = _read_edid_capabilities(bytes(base) + bytes(cea))
        assert caps.max_luminance == pytest.approx(295.2, abs=0.5)
        assert caps.min_luminance == pytest.approx(0.298, abs=0.005)

    def test_old_edid_version_ignores_depth(self):
        edid = _make_base_edid(version=(1, 3), depth_code=3)
        caps = _read_edid_capabilities(edid)
        assert caps.ten_bit is False

    def test_short_edid(self):
        caps = _read_edid_capabilities(b"\x00" * 64)
        assert caps == _EdidCaps(ten_bit=False, hdr=False)

    def test_empty_edid(self):
        caps = _read_edid_capabilities(b"")
        assert caps == _EdidCaps(ten_bit=False, hdr=False)


class TestDrmVrrCapable:
    def test_no_drm_device(self):
        with patch("hyprland_monitors.hardware._open_drm", side_effect=OSError):
            assert _read_drm_vrr_capable("DP-1", "card0") is False


class TestListConnectors:
    def test_returns_connected_and_disconnected(self, tmp_path, clear_card_cache):
        drm = _build_fake_drm(tmp_path, {"DP-1": "connected", "HDMI-A-1": "disconnected"})
        with patch("hyprland_monitors.hardware._DRM_DIR", drm):
            result = list_connectors()
        assert result == {"DP-1": True, "HDMI-A-1": False}

    def test_empty_when_no_card(self):
        with patch("hyprland_monitors.hardware._find_primary_card", return_value=None):
            assert list_connectors() == {}

    def test_returns_dict_with_correct_types(self, tmp_path, clear_card_cache):
        drm = _build_fake_drm(tmp_path, {"DP-1": "connected"})
        with patch("hyprland_monitors.hardware._DRM_DIR", drm):
            result = list_connectors()
        for name, connected in result.items():
            assert isinstance(name, str)
            assert isinstance(connected, bool)


class TestGetMonitorCapabilities:
    def test_not_found_returns_none(self):
        with patch("hyprland_monitors.hardware._find_drm_connector", return_value=None):
            assert get_monitor_capabilities("FAKE-99") is None

    def test_reads_edid_and_vrr(self, tmp_path, clear_card_cache):
        """Full integration: fake sysfs connector with EDID file."""
        drm = _build_fake_drm(tmp_path, {"DP-1": "connected"})
        edid = _make_base_edid(depth_code=3, num_extensions=1)
        cea = _make_cea_extension(has_hdr=True)
        (drm / "card0-DP-1" / "edid").write_bytes(bytes(edid) + bytes(cea))

        with (
            patch("hyprland_monitors.hardware._DRM_DIR", drm),
            patch("hyprland_monitors.hardware._read_drm_vrr_capable", return_value=False),
        ):
            caps = get_monitor_capabilities("DP-1")

        assert caps is not None
        assert caps["ten_bit"] is True
        assert caps["hdr"] is True
        assert caps["vrr"] is False
