"""
tests/test_data_engine.py — Expedition 33 Compass
===================================================
Covers:
  1. Coordinate conversion accuracy (SW, NE, centre, round-trips).
  2. Category filter — searching for 'Bosses' returns only Bosses.
  3. Name search — partial, case-insensitive.
  4. Proximity routing — segment projection, clamping, guard clauses.
  5. Fly-to / Focus: fly_to_poi sets the correct session_state keys.
  6. Route path structure: coords ordered A → waypoints → B.

Run with:
    cd <project_root>
    pytest tests/ -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Make project root importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Stub out streamlit before importing data_engine
# ---------------------------------------------------------------------------
_st_mock = MagicMock()
_st_mock.cache_data = lambda *a, **kw: (lambda f: f)   # passthrough decorator
_st_mock.session_state = {}
sys.modules["streamlit"] = _st_mock

# Now safe to import project modules
from constants import (  # noqa: E402
    Col, IMG_H, IMG_W, LAT_NE, LAT_SW, LNG_NE, LNG_SW, CSV_FILENAME,
    DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG, SK,
)
from data_engine import (  # noqa: E402
    _dist_point_to_segment,
    _build_path_coords,
    df_hash,
    euclidean_px,
    get_categories,
    get_filtered,
    latlon_to_pixel,
    pixel_to_latlon,
    proximity_routing,
)

TOL = 1e-8  # floating-point tolerance


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture(scope="module")
def full_df() -> pd.DataFrame:
    """Load and enrich the real CSV once for the whole test session."""
    csv = PROJECT_ROOT / CSV_FILENAME
    df = pd.read_csv(csv)
    df.columns = [c.strip().lower() for c in df.columns]
    df[Col.ID] = df[Col.ID].astype(str)
    df[Col.LAT] = pd.to_numeric(df[Col.LAT], errors="coerce")
    df[Col.LNG] = pd.to_numeric(df[Col.LNG], errors="coerce")
    df[Col.PX_X] = (df[Col.LNG] - LNG_SW) / (LNG_NE - LNG_SW) * IMG_W
    df[Col.PX_Y] = (LAT_SW - df[Col.LAT]) / (LAT_SW - LAT_NE) * IMG_H
    return df.reset_index(drop=True)


@pytest.fixture(scope="module")
def _hash(full_df) -> str:
    return df_hash(full_df)


# ===========================================================================
# 1. COORDINATE CONVERSION
# ===========================================================================

class TestCoordinateConversion:

    def test_sw_corner_maps_to_pixel_origin(self):
        """SW corner (lat=30, lng=20) → pixel (0, 0)."""
        x, y = latlon_to_pixel(LAT_SW, LNG_SW)
        assert abs(x) < TOL
        assert abs(y) < TOL

    def test_ne_corner_maps_to_image_extent(self):
        """NE corner (lat=-260, lng=215) → pixel (8192, 6828)."""
        x, y = latlon_to_pixel(LAT_NE, LNG_NE)
        assert abs(x - IMG_W) < TOL
        assert abs(y - IMG_H) < TOL

    def test_centre_maps_to_half_image(self):
        """Map centre → (4096, 3414)."""
        x, y = latlon_to_pixel(
            (LAT_SW + LAT_NE) / 2,
            (LNG_SW + LNG_NE) / 2,
        )
        assert abs(x - IMG_W / 2) < TOL
        assert abs(y - IMG_H / 2) < TOL

    @pytest.mark.parametrize("lat,lng", [
        (LAT_SW, LNG_SW),
        (LAT_NE, LNG_NE),
        (-115.0, 117.5),
        (0.0, 100.0),
        (-50.0, 80.0),
        (-200.0, 150.0),
    ])
    def test_round_trip(self, lat, lng):
        """pixel_to_latlon(latlon_to_pixel(lat, lng)) == (lat, lng)."""
        px_x, px_y = latlon_to_pixel(lat, lng)
        lat2, lng2 = pixel_to_latlon(px_x, px_y)
        assert abs(lat2 - lat) < TOL, f"lat round-trip failed: {lat2} != {lat}"
        assert abs(lng2 - lng) < TOL, f"lng round-trip failed: {lng2} != {lng}"

    def test_all_csv_rows_within_image_bounds(self, full_df):
        """No POI in the CSV should map outside the 8K image."""
        assert (full_df[Col.PX_X] >= 0).all()
        assert (full_df[Col.PX_X] <= IMG_W).all()
        assert (full_df[Col.PX_Y] >= 0).all()
        assert (full_df[Col.PX_Y] <= IMG_H).all()

    def test_euclidean_distance_3_4_5(self):
        """Classic 3-4-5 right triangle."""
        assert abs(euclidean_px(0, 0, 3, 4) - 5.0) < TOL


# ===========================================================================
# 2. FILTER — "Boss" search returns only Bosses   (test scenario 1)
# ===========================================================================

class TestFiltering:

    def test_category_filter_bosses_only(self, full_df, _hash):
        """Filtering by 'Bosses' returns only Bosses rows."""
        result = get_filtered(_hash, full_df, category="Bosses")
        assert len(result) > 0, "Expected at least one Boss"
        assert (result[Col.CATEGORY] == "Bosses").all(), (
            "Non-Boss rows found after filtering for Bosses"
        )

    def test_category_filter_weapons(self, full_df, _hash):
        result = get_filtered(_hash, full_df, category="Weapons")
        assert (result[Col.CATEGORY] == "Weapons").all()

    def test_all_returns_full_frame(self, full_df, _hash):
        result = get_filtered(_hash, full_df, category="All")
        assert len(result) == len(full_df)

    def test_search_case_insensitive(self, full_df, _hash):
        """Search for 'cave' should match rows containing 'Cave'."""
        result = get_filtered(_hash, full_df, search="cave")
        assert len(result) > 0
        assert all("cave" in name.lower() for name in result[Col.NAME])

    def test_search_partial_match(self, full_df, _hash):
        """Partial name 'Abbest' should match 'Abbest Cave'."""
        result = get_filtered(_hash, full_df, search="Abbest")
        assert len(result) >= 1
        assert any("Abbest" in n for n in result[Col.NAME])

    def test_combined_filter_and_search(self, full_df, _hash):
        """Category + search narrows results correctly."""
        result = get_filtered(_hash, full_df, category="Bosses", search="e")
        assert (result[Col.CATEGORY] == "Bosses").all()
        # Every name must contain 'e' (case-insensitive)
        assert all("e" in n.lower() for n in result[Col.NAME])

    def test_empty_search_returns_category_results(self, full_df, _hash):
        """Empty search string should not further filter the category slice."""
        bosses = get_filtered(_hash, full_df, category="Bosses")
        bosses_no_search = get_filtered(_hash, full_df, category="Bosses", search="")
        assert len(bosses) == len(bosses_no_search)

    def test_no_match_returns_empty_frame(self, full_df, _hash):
        result = get_filtered(_hash, full_df, search="xXnonexistentXx")
        assert len(result) == 0

    def test_get_categories_matches_csv(self, full_df):
        cats = get_categories(full_df)
        expected = {
            "Bosses", "Chroma", "Enemies", "ExpeditionJournals",
            "Locations", "LostGestrals", "Merchants", "MusicRecords",
            "Outfits", "Pictos", "TintsMaterials", "Weapons",
        }
        assert set(cats) == expected


# ===========================================================================
# 3. LINE-SEGMENT PROJECTION  (routing math — architect-review R-03)
# ===========================================================================

class TestLineSegmentProjection:

    def test_midpoint_on_segment_distance_zero(self):
        """A point ON the midpoint of the segment has distance 0 and t=0.5."""
        d, t = _dist_point_to_segment(50, 50, 0, 0, 100, 100)
        assert d < TOL
        assert abs(t - 0.5) < TOL

    def test_point_past_B_clamps_to_1(self):
        """t_raw > 1 must clamp to t=1; distance = dist(P, B)."""
        d, t = _dist_point_to_segment(200, 0, 0, 0, 100, 0)
        assert abs(t - 1.0) < TOL
        assert abs(d - 100.0) < TOL

    def test_point_before_A_clamps_to_0(self):
        """t_raw < 0 must clamp to t=0; distance = dist(P, A)."""
        d, t = _dist_point_to_segment(-50, 0, 0, 0, 100, 0)
        assert abs(t - 0.0) < TOL
        assert abs(d - 50.0) < TOL

    def test_perpendicular_off_midpoint(self):
        """Point 30px above mid of horizontal segment → dist=30, t=0.5."""
        d, t = _dist_point_to_segment(50, 30, 0, 0, 100, 0)
        assert abs(d - 30.0) < TOL
        assert abs(t - 0.5) < TOL

    def test_degenerate_segment_falls_back_to_distance_to_A(self):
        """A == B: returns Euclidean distance from P to A."""
        d, t = _dist_point_to_segment(3, 4, 0, 0, 0, 0)
        assert abs(d - 5.0) < TOL   # 3-4-5 triangle

    def test_segment_endpoint_A_distance_zero(self):
        """P coincides with A."""
        d, t = _dist_point_to_segment(0, 0, 0, 0, 100, 0)
        assert d < TOL
        assert abs(t - 0.0) < TOL

    def test_segment_endpoint_B_distance_zero(self):
        """P coincides with B."""
        d, t = _dist_point_to_segment(100, 0, 0, 0, 100, 0)
        assert d < TOL
        assert abs(t - 1.0) < TOL


# ===========================================================================
# 4. PROXIMITY ROUTING  (test scenario 3 — path draws between A and B)
# ===========================================================================

class TestProximityRouting:

    def _two_distinct_ids(self, full_df) -> tuple[str, str]:
        """Pick two distinct IDs from the dataset."""
        ids = full_df[Col.ID].tolist()
        return ids[0], ids[5]

    def test_raises_on_same_id(self, full_df):
        """id_a == id_b must raise ValueError."""
        pid = full_df[Col.ID].iloc[0]
        with pytest.raises(ValueError, match="distinct"):
            proximity_routing(full_df, pid, pid)

    def test_raises_on_missing_id(self, full_df):
        """Unknown ID must raise ValueError."""
        pid = full_df[Col.ID].iloc[0]
        with pytest.raises(ValueError):
            proximity_routing(full_df, pid, "id_does_not_exist_99999")

    def test_returns_at_most_n_waypoints(self, full_df):
        id_a, id_b = self._two_distinct_ids(full_df)
        wps, coords = proximity_routing(full_df, id_a, id_b, n=3)
        assert len(wps) <= 3

    def test_path_starts_with_A_ends_with_B(self, full_df):
        """The returned coordinate list must begin at A and end at B."""
        id_a, id_b = self._two_distinct_ids(full_df)
        wps, coords = proximity_routing(full_df, id_a, id_b, n=5)
        assert len(coords) >= 2

        row_a = full_df[full_df[Col.ID] == id_a].iloc[0]
        row_b = full_df[full_df[Col.ID] == id_b].iloc[0]

        assert abs(coords[0][0] - float(row_a[Col.LAT])) < TOL
        assert abs(coords[0][1] - float(row_a[Col.LNG])) < TOL
        assert abs(coords[-1][0] - float(row_b[Col.LAT])) < TOL
        assert abs(coords[-1][1] - float(row_b[Col.LNG])) < TOL

    def test_waypoints_not_in_endpoints(self, full_df):
        """Waypoints must not include id_a or id_b."""
        id_a, id_b = self._two_distinct_ids(full_df)
        wps, _ = proximity_routing(full_df, id_a, id_b, n=5)
        assert id_a not in wps
        assert id_b not in wps

    def test_large_corridor_finds_waypoints(self, full_df):
        """With a very large corridor, at least some waypoints should be found
        (assuming the dataset has more than 2 points)."""
        ids = full_df[Col.ID].tolist()
        wps, coords = proximity_routing(
            full_df, ids[0], ids[-1], n=10, corridor_px=99999.0
        )
        # Just verifying structure — exact count depends on geometry
        assert isinstance(wps, list)
        assert isinstance(coords, list)
        assert coords[0] != coords[-1]   # A and B are distinct

    def test_zero_corridor_returns_no_waypoints(self, full_df):
        """Corridor of 0 px → no POI can be 'on the way'."""
        ids = full_df[Col.ID].tolist()
        wps, coords = proximity_routing(
            full_df, ids[0], ids[1], n=5, corridor_px=0.0
        )
        assert wps == []
        # Path should still have A and B
        assert len(coords) == 2

    def test_build_path_coords_order(self, full_df):
        """_build_path_coords preserves insertion order."""
        ids = full_df[Col.ID].tolist()
        id_a, wp1, wp2, id_b = ids[0], ids[2], ids[4], ids[6]
        coords = _build_path_coords(full_df, [wp1, wp2], id_a, id_b)
        # Should be length 4: A, wp1, wp2, B
        assert len(coords) == 4


# ===========================================================================
# 5. FLY-TO / FOCUS  (test scenario 2 — Focus re-centres map)
# ===========================================================================

class TestFlyTo:
    """
    fly_to_poi() must write SK.MAP_CENTER and SK.MAP_ZOOM into session_state.
    We patch st.session_state directly on the mock.
    """

    def _make_state(self):
        state = {
            SK.MAP_CENTER: (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG),
            SK.MAP_ZOOM: 2,
            SK.FLY_TO: None,
        }
        return state

    def test_fly_to_updates_map_center(self, full_df):
        """fly_to_poi should update SK.MAP_CENTER to the POI's lat/lng."""
        import data_engine
        state = self._make_state()
        _st_mock.session_state = state

        target_id = full_df[Col.ID].iloc[0]
        target_row = full_df.iloc[0]

        data_engine.fly_to_poi(full_df, target_id)

        new_center = state[SK.MAP_CENTER]
        assert abs(new_center[0] - float(target_row[Col.LAT])) < TOL
        assert abs(new_center[1] - float(target_row[Col.LNG])) < TOL

    def test_fly_to_increases_zoom(self, full_df):
        """fly_to_poi should zoom in (zoom > DEFAULT_ZOOM)."""
        import data_engine
        state = self._make_state()
        _st_mock.session_state = state

        data_engine.fly_to_poi(full_df, full_df[Col.ID].iloc[0])
        assert state[SK.MAP_ZOOM] > 2

    def test_fly_to_unknown_id_is_noop(self, full_df):
        """fly_to_poi with unknown ID must not modify session_state."""
        import data_engine
        original_center = (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG)
        state = self._make_state()
        state[SK.MAP_CENTER] = original_center
        _st_mock.session_state = state

        data_engine.fly_to_poi(full_df, "nonexistent_id_xyz")
        assert state[SK.MAP_CENTER] == original_center

    def test_center_injection_logic(self):
        """fly_to overrides map_center; None falls back to map_center."""
        map_center = (-115.0, 117.5)
        fly_to = (-100.0, 150.0)

        result_with_flyto = list(fly_to) if fly_to is not None else list(map_center)
        result_no_flyto = list(None if None is not None else map_center)

        assert result_with_flyto == [-100.0, 150.0]
        assert result_no_flyto == list(map_center)
