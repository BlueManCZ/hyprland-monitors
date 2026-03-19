"""Tests for monitor scaling and neighbor adjustment logic."""

import pytest

from hyprland_monitors import (
    MonitorState,
    ScaleOption,
    adjust_neighbors,
    all_monitors_connected,
    compute_valid_scales,
    is_adjacent,
    lines_from_monitors,
    merge_saved_state,
    nearest_scale_index,
    parse_extras,
    parse_mode,
)


def _make_monitor(name, w, h, x, y, scale=1.0, transform=0):
    return MonitorState(
        name=name,
        make="",
        model="",
        width=w,
        height=h,
        refresh_rate=60.0,
        x=x,
        y=y,
        scale=scale,
        transform=transform,
    )


def _scale_values(w, h, **kwargs):
    """Helper: return just the precise scale values (not labels)."""
    return [s.value for s in compute_valid_scales(w, h, **kwargs)]


def _scale_labels(w, h, **kwargs):
    """Helper: return just the display labels."""
    return [s.label for s in compute_valid_scales(w, h, **kwargs)]


class TestComputeValidScales:
    def test_1_0_always_valid(self):
        assert 1.0 in _scale_values(1920, 1080)

    def test_2_0_always_valid(self):
        assert 2.0 in _scale_values(1920, 1080)

    def test_1080p_has_1_5(self):
        labels = _scale_labels(1920, 1080)
        assert "1.5" in labels

    def test_3440x1440_rejects_1_5(self):
        labels = _scale_labels(3440, 1440)
        assert "1.5" not in labels

    def test_3440x1440_accepts_1_6(self):
        assert 1.6 in _scale_values(3440, 1440)

    def test_3440x1440_accepts_1_25(self):
        assert 1.25 in _scale_values(3440, 1440)

    def test_3440x1440_labels_match_hyprland(self):
        """Display labels must match what Hyprland reports (filtered by min_effective)."""
        labels = _scale_labels(3440, 1440)
        expected = [
            "0.5",
            "0.53",
            "0.62",
            "0.67",
            "0.8",
            "0.83",
            "1",
            "1.07",
            "1.25",
            "1.33",
            "1.6",
            "1.67",
            "2",
            "2.5",
            "2.67",
        ]
        assert labels == expected

    def test_precise_values_are_120th_multiples(self):
        """Full-precision scale values are exact multiples of 1/120."""
        for s, _ in compute_valid_scales(1920, 1080):
            tick = s * 120
            assert abs(tick - round(tick)) < 1e-9, f"{s} is not a multiple of 1/120"

    def test_sorted(self):
        values = _scale_values(1920, 1080)
        assert values == sorted(values)

    def test_no_duplicate_labels(self):
        labels = _scale_labels(3440, 1440)
        assert len(labels) == len(set(labels))

    def test_within_bounds(self):
        values = _scale_values(1920, 1080, lo=1.0, hi=2.0)
        assert all(1.0 <= s <= 2.0 for s in values)


class TestNearestScaleIndex:
    def test_exact_match(self):
        scales = [
            ScaleOption(1.0, "1"),
            ScaleOption(1.25, "1.25"),
            ScaleOption(1.5, "1.5"),
            ScaleOption(2.0, "2"),
        ]
        assert nearest_scale_index(scales, 1.5) == 2

    def test_nearest(self):
        scales = [
            ScaleOption(1.0, "1"),
            ScaleOption(1.25, "1.25"),
            ScaleOption(1.6, "1.6"),
            ScaleOption(2.0, "2"),
        ]
        assert nearest_scale_index(scales, 1.5) == 2

    def test_first(self):
        scales = [ScaleOption(1.0, "1"), ScaleOption(1.5, "1.5"), ScaleOption(2.0, "2")]
        assert nearest_scale_index(scales, 0.8) == 0

    def test_last(self):
        scales = [ScaleOption(1.0, "1"), ScaleOption(1.5, "1.5"), ScaleOption(2.0, "2")]
        assert nearest_scale_index(scales, 3.0) == 2

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            nearest_scale_index([], 1.0)


class TestEffectiveSize:
    def test_no_scale_no_transform(self):
        mon = _make_monitor("A", 1920, 1080, 0, 0)
        assert mon.effective_size == (1920, 1080)

    def test_scale_2(self):
        mon = _make_monitor("A", 1920, 1080, 0, 0, scale=2.0)
        assert mon.effective_size == (960, 540)

    def test_scale_1_6_ultrawide(self):
        mon = _make_monitor("A", 3440, 1440, 0, 0, scale=1.6)
        assert mon.effective_size == (2150, 900)

    def test_transform_90(self):
        mon = _make_monitor("A", 1920, 1080, 0, 0, transform=1)
        assert mon.effective_size == (1080, 1920)

    def test_transform_90_with_scale(self):
        mon = _make_monitor("A", 1920, 1080, 0, 0, scale=2.0, transform=1)
        assert mon.effective_size == (540, 960)


class TestIsAdjacent:
    def test_horizontal_adjacent(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 1920, 0)
        assert is_adjacent(a, b) is True

    def test_horizontal_gap(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 2020, 0)
        assert is_adjacent(a, b) is False

    def test_vertical_adjacent(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 0, 1080)
        assert is_adjacent(a, b) is True

    def test_vertical_gap(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 0, 1180)
        assert is_adjacent(a, b) is False

    def test_overlapping_not_touching(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 100, 100)
        assert is_adjacent(a, b) is False

    def test_corner_only_not_touching(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 1920, 1080)
        assert is_adjacent(a, b) is False

    def test_partial_edge_overlap(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 1920, 500)
        assert is_adjacent(a, b) is True

    def test_symmetric(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 1920, 0)
        assert is_adjacent(a, b) == is_adjacent(b, a)

    def test_with_scale(self):
        a = _make_monitor("A", 3440, 1440, 0, 0, scale=2.0)
        b = _make_monitor("B", 1920, 1080, 1720, 0)
        assert is_adjacent(a, b) is True

    def test_with_transform_90(self):
        a = _make_monitor("A", 1920, 1080, 0, 0, transform=1)
        b = _make_monitor("B", 1920, 1080, 1080, 0)
        assert is_adjacent(a, b) is True


class TestAllMonitorsConnected:
    def test_empty(self):
        assert all_monitors_connected([]) is True

    def test_single(self):
        assert all_monitors_connected([_make_monitor("A", 1920, 1080, 0, 0)]) is True

    def test_two_adjacent(self):
        monitors = [
            _make_monitor("A", 1920, 1080, 0, 0),
            _make_monitor("B", 1920, 1080, 1920, 0),
        ]
        assert all_monitors_connected(monitors) is True

    def test_two_disconnected(self):
        monitors = [
            _make_monitor("A", 1920, 1080, 0, 0),
            _make_monitor("B", 1920, 1080, 5000, 0),
        ]
        assert all_monitors_connected(monitors) is False

    def test_three_chain(self):
        monitors = [
            _make_monitor("A", 1920, 1080, 0, 0),
            _make_monitor("B", 1920, 1080, 1920, 0),
            _make_monitor("C", 1920, 1080, 3840, 0),
        ]
        assert all_monitors_connected(monitors) is True

    def test_three_with_island(self):
        monitors = [
            _make_monitor("A", 1920, 1080, 0, 0),
            _make_monitor("B", 1920, 1080, 1920, 0),
            _make_monitor("C", 1920, 1080, 8000, 0),
        ]
        assert all_monitors_connected(monitors) is False

    def test_disabled_monitors_ignored(self):
        a = _make_monitor("A", 1920, 1080, 0, 0)
        b = _make_monitor("B", 1920, 1080, 8000, 0)
        b.disabled = True
        assert all_monitors_connected([a, b]) is True

    def test_l_shape_connected(self):
        monitors = [
            _make_monitor("A", 1920, 1080, 0, 0),
            _make_monitor("B", 1920, 1080, 1920, 0),
            _make_monitor("C", 1920, 1080, 1920, 1080),
        ]
        assert all_monitors_connected(monitors) is True


def _do_adjust(monitors, changed_idx, new_vals):
    """Apply new values to a monitor and adjust neighbors."""
    mon = monitors[changed_idx]
    old_w, old_h = mon.effective_size
    for k, v in new_vals.items():
        setattr(mon, k, v)
    adjust_neighbors(monitors, mon, old_w, old_h)


class TestAdjustNeighbors:
    """Test the neighbor snapping logic."""

    def test_scale_up_shifts_right_neighbor(self):
        mon_a = _make_monitor("A", 3440, 1440, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 3440, 0, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 2.0})
        assert mon_b.x == 1720

    def test_scale_down_shifts_right_neighbor(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=2.0)
        mon_b = _make_monitor("B", 1920, 1080, 960, 0, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 1.0})
        assert mon_b.x == 1920

    def test_touching_monitors_stay_touching(self):
        mon_a = _make_monitor("A", 3440, 1440, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 3440, 0, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 1.6})
        a_right = mon_a.x + mon_a.effective_size[0]
        assert mon_b.x == a_right

    def test_gap_preserved(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 2020, 0, scale=1.0)  # 100px gap
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 2.0})
        assert mon_b.x == 1060

    def test_left_neighbor_not_moved(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 1920, 0, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 1, {"scale": 2.0})
        assert mon_a.x == 0

    def test_vertical_neighbor_below(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 0, 1080, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 2.0})
        assert mon_b.y == 540

    def test_three_monitors_chain(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 1920, 0, scale=1.0)
        mon_c = _make_monitor("C", 1920, 1080, 3840, 0, scale=1.0)
        monitors = [mon_a, mon_b, mon_c]
        _do_adjust(monitors, 0, {"scale": 2.0})
        assert mon_b.x == 960
        assert mon_c.x == 2880

    def test_transform_90_changes_effective_size(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, transform=0)
        mon_b = _make_monitor("B", 1920, 1080, 1920, 0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"transform": 1})
        assert mon_b.x == 1080

    def test_no_change_no_shift(self):
        mon_a = _make_monitor("A", 1920, 1080, 0, 0, scale=1.0)
        mon_b = _make_monitor("B", 1920, 1080, 1920, 0, scale=1.0)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 1.0})
        assert mon_b.x == 1920

    def test_real_setup_3440_1920_vertical_offset(self):
        mon_a = _make_monitor("DP-2", 3440, 1440, 0, 0, scale=1.0)
        mon_b = _make_monitor("DP-3", 1920, 1080, 3440, -450, scale=1.0, transform=3)
        monitors = [mon_a, mon_b]
        _do_adjust(monitors, 0, {"scale": 1.6})
        assert mon_b.x == 2150
        assert mon_b.y == -450


class TestLinesFromMonitors:
    def test_single_monitor(self):
        mon = _make_monitor("DP-1", 1920, 1080, 0, 0, scale=1.0)
        lines = lines_from_monitors([mon])
        assert lines == ["DP-1, 1920x1080@60.00Hz, 0x0, 1"]

    def test_two_monitors(self):
        mon_a = _make_monitor("DP-1", 3440, 1440, 0, 0, scale=1.6)
        mon_a.refresh_rate = 99.98
        mon_b = _make_monitor("DP-2", 1920, 1080, 2150, -450, scale=1.0, transform=3)
        lines = lines_from_monitors([mon_a, mon_b])
        assert len(lines) == 2
        assert "3440x1440@99.98Hz" in lines[0]
        assert "transform, 3" in lines[1]

    def test_with_bit_depth(self):
        mon = _make_monitor("DP-1", 3440, 1440, 0, 0, scale=1.0)
        mon.refresh_rate = 165.0
        mon.bit_depth = "10"
        lines = lines_from_monitors([mon])
        assert "bitdepth, 10" in lines[0]

    def test_with_vrr(self):
        mon = _make_monitor("DP-1", 1920, 1080, 0, 0, scale=1.0)
        mon.vrr = "1"
        lines = lines_from_monitors([mon])
        assert "vrr, 1" in lines[0]

    def test_with_color_management(self):
        mon = _make_monitor("DP-1", 3440, 1440, 0, 0, scale=1.0)
        mon.refresh_rate = 165.0
        mon.color_management = "srgb"
        lines = lines_from_monitors([mon])
        assert "cm, srgb" in lines[0]

    def test_all_extras(self):
        mon = _make_monitor("DP-2", 3440, 1440, 0, 0, scale=1.0)
        mon.refresh_rate = 165.0
        mon.bit_depth = "10"
        mon.vrr = "1"
        mon.color_management = "hdr"
        lines = lines_from_monitors([mon])
        assert lines[0] == "DP-2, 3440x1440@165.00Hz, 0x0, 1, bitdepth, 10, vrr, 1, cm, hdr"

    def test_no_extras_when_none(self):
        mon = _make_monitor("DP-1", 1920, 1080, 0, 0, scale=1.0)
        lines = lines_from_monitors([mon])
        assert lines[0] == "DP-1, 1920x1080@60.00Hz, 0x0, 1"

    def test_disabled_monitor(self):
        mon = _make_monitor("DP-1", 1920, 1080, 0, 0, scale=1.0)
        mon.disabled = True
        lines = lines_from_monitors([mon])
        assert lines == ["DP-1, disable"]


class TestParseExtras:
    def test_bit_depth(self):
        line = "DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10"
        assert parse_extras(line) == {"bit_depth": "10"}

    def test_color_management(self):
        line = "DP-2, 3440x1440@165, 0x0, 1, cm, srgb"
        assert parse_extras(line) == {"color_management": "srgb"}

    def test_vrr(self):
        line = "DP-1, 1920x1080@60, 0x0, 1, vrr, 2"
        assert parse_extras(line) == {"vrr": "2"}

    def test_multiple_extras(self):
        line = "DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10, cm, srgb"
        extras = parse_extras(line)
        assert extras == {"bit_depth": "10", "color_management": "srgb"}

    def test_transform_skipped(self):
        line = "DP-3, 1920x1080@75, 3440x-450, 1, transform, 3"
        assert parse_extras(line) == {}

    def test_all_extras_with_transform(self):
        line = "DP-2, 3440x1440@165, 0x0, 1, transform, 0, bitdepth, 10, vrr, 1, cm, hdr"
        extras = parse_extras(line)
        assert extras == {"bit_depth": "10", "vrr": "1", "color_management": "hdr"}

    def test_no_extras(self):
        line = "DP-1, 1920x1080@60, 0x0, 1"
        assert parse_extras(line) == {}


class TestMergeSavedState:
    def test_merges_bit_depth(self):
        monitors = [_make_monitor("DP-2", 3440, 1440, 0, 0)]
        saved = ["monitor = DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10"]
        merge_saved_state(monitors, saved)
        assert monitors[0].bit_depth == "10"

    def test_saved_overrides_ipc(self):
        mon = _make_monitor("DP-2", 3440, 1440, 0, 0)
        mon.bit_depth = "8"
        saved = ["monitor = DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10"]
        merge_saved_state([mon], saved)
        assert mon.bit_depth == "10"

    def test_unmatched_monitor_ignored(self):
        monitors = [_make_monitor("DP-1", 1920, 1080, 0, 0)]
        saved = ["monitor = DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10"]
        merge_saved_state(monitors, saved)
        assert monitors[0].bit_depth is None

    def test_restores_disabled_flag(self):
        mon = _make_monitor("DP-1", 1920, 1080, 0, 0)
        assert mon.disabled is False
        merge_saved_state([mon], ["monitor = DP-1, disable"])
        assert mon.disabled is True

    def test_multiple_monitors(self):
        monitors = [
            _make_monitor("DP-2", 3440, 1440, 0, 0),
            _make_monitor("DP-3", 1920, 1080, 0, 0),
        ]
        saved = [
            "monitor = DP-2, 3440x1440@165, 0x0, 1, bitdepth, 10, cm, srgb",
            "monitor = DP-3, 1920x1080@75, 3440x-450, 1, transform, 3",
        ]
        merge_saved_state(monitors, saved)
        assert monitors[0].bit_depth == "10"
        assert monitors[0].color_management == "srgb"
        assert monitors[1].bit_depth is None


class TestParseMode:
    def test_basic(self):
        result = parse_mode("1920x1080@60.00Hz")
        assert result == {"width": 1920, "height": 1080, "refresh_rate": 60.0}

    def test_with_space(self):
        result = parse_mode("3440x1440@99.98 Hz")
        assert result == {"width": 3440, "height": 1440, "refresh_rate": 99.98}

    def test_high_refresh(self):
        result = parse_mode("2560x1440@165.00Hz")
        assert result == {"width": 2560, "height": 1440, "refresh_rate": 165.0}
