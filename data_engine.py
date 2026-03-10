"""
data_engine.py — Expedition 33 Compass
=======================================
Pure data layer.  Zero Streamlit UI code.  Zero Folium imports.
Fully testable in isolation with pytest.

Responsibilities
----------------
  1. Load and validate the CSV exactly once (Layer-1 cache).
  2. Convert lat/lng → pixel coordinates with the canonical formula.
  3. Expose a filtered view of the data (Layer-2 cache).
  4. Initialise all `st.session_state` keys under the "compass." namespace.
  5. Compute proximity routing with hardened line-segment projection.
"""
from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

from constants import (
    Col,
    CSV_FILENAME,
    IMG_H,
    IMG_W,
    LAT_NE,
    LAT_SW,
    LNG_NE,
    LNG_SW,
    OFFSET_X_DEFAULT,
    OFFSET_Y_DEFAULT,
    ROUTE_CORRIDOR_PX,
    SK,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default CSV path (relative to this file's directory)
# ---------------------------------------------------------------------------
_DEFAULT_CSV = Path(__file__).parent / CSV_FILENAME


# ===========================================================================
# 1.  COORDINATE CONVERSION  (pure functions — no side-effects, easily tested)
# ===========================================================================

def latlon_to_pixel(
    lat: float,
    lng: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[float, float]:
    """
    Convert a CRS.Simple (lat, lng) coordinate to image pixel (px_x, px_y).

    Formula with optional frame offset
    ------------------------------------
      px_x = OFFSET_X + (lng - LNG_SW) / (LNG_NE - LNG_SW) * (IMG_W - 2*OFFSET_X)
      px_y = OFFSET_Y + (LAT_SW - lat) / (LAT_SW - LAT_NE) * (IMG_H - 2*OFFSET_Y)

    When offset_x = offset_y = 0 this reduces to the original canonical formula.
    Non-zero offsets account for a visual frame/border in the source image:
    the data range still maps to [LNG_SW, LNG_NE] × [LAT_NE, LAT_SW] in
    coordinate space, but now lands on the *inner* pixel area
    [OFFSET_X : IMG_W-OFFSET_X] × [OFFSET_Y : IMG_H-OFFSET_Y].

    Parameters
    ----------
    lat      : CRS.Simple latitude   (vertical axis,   range −260 … 30)
    lng      : CRS.Simple longitude  (horizontal axis, range   20 … 215)
    offset_x : Frame width in pixels on the left AND right edges (default 0).
    offset_y : Frame height in pixels on the top  AND bottom edges (default 0).

    Returns
    -------
    (px_x, px_y) — pixel column and row (floats; callers may round).
    """
    active_w = IMG_W - 2.0 * offset_x
    active_h = IMG_H - 2.0 * offset_y
    px_x = offset_x + (lng - LNG_SW) / (LNG_NE - LNG_SW) * active_w
    px_y = offset_y + (LAT_SW - lat) / (LAT_SW - LAT_NE) * active_h
    return px_x, px_y


def pixel_to_latlon(
    px_x: float,
    px_y: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[float, float]:
    """
    Inverse of :func:`latlon_to_pixel` — accounts for the same frame offset.

    Useful for round-trip tests and for debugging clicked pixel positions.
    """
    active_w = IMG_W - 2.0 * offset_x
    active_h = IMG_H - 2.0 * offset_y
    lng = (px_x - offset_x) / active_w * (LNG_NE - LNG_SW) + LNG_SW
    lat = LAT_SW - (px_y - offset_y) / active_h * (LAT_SW - LAT_NE)
    return lat, lng


def compute_pixels(
    df: pd.DataFrame,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> pd.DataFrame:
    """
    Return a *copy* of *df* with ``px_x`` and ``px_y`` recomputed using
    the given frame offsets.

    This is intentionally NOT cached — it's called only inside
    :func:`proximity_routing` when non-zero offsets are active, and is
    cheap (vectorised NumPy over 374 rows).  Routing distances will then
    reflect the offset-adjusted pixel space, giving correct "closeness"
    results even after calibration.

    Parameters
    ----------
    df       : DataFrame with Col.LAT ("x") and Col.LNG ("y") columns.
    offset_x : Horizontal frame offset in pixels.
    offset_y : Vertical   frame offset in pixels.
    """
    active_w = IMG_W - 2.0 * offset_x
    active_h = IMG_H - 2.0 * offset_y
    out = df.copy()
    out[Col.PX_X] = offset_x + (df[Col.LNG] - LNG_SW) / (LNG_NE - LNG_SW) * active_w
    out[Col.PX_Y] = offset_y + (LAT_SW - df[Col.LAT]) / (LAT_SW - LAT_NE) * active_h
    return out


def euclidean_px(ax: float, ay: float, bx: float, by: float) -> float:
    """Euclidean distance between two pixel-space points."""
    return math.hypot(bx - ax, by - ay)


# ---------------------------------------------------------------------------
# Internal helper: perpendicular distance from point P to segment A→B
# using clamped parametric projection (t ∈ [0, 1]).
# ---------------------------------------------------------------------------

def _dist_point_to_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> tuple[float, float]:
    """
    Compute:
      1. The *clamped* perpendicular distance from point P=(px,py) to the
         line segment A=(ax,ay)→B=(bx,by).
      2. The scalar projection `t` ∈ [0, 1] of P onto A→B (used to sort
         waypoints along the path direction).

    Clamping t to [0,1] ensures we only consider POIs that are geometrically
    *between* A and B, not behind A or past B on the infinite line.

    Returns
    -------
    (perp_distance, t_along_segment)
    """
    abx, aby = bx - ax, by - ay
    ab_len_sq = abx * abx + aby * aby

    if ab_len_sq < 1e-10:
        # A and B are the same pixel — degenerate segment
        return euclidean_px(px, py, ax, ay), 0.0

    # Scalar projection parameter (unclamped)
    t_raw = ((px - ax) * abx + (py - ay) * aby) / ab_len_sq

    # Clamp to [0, 1]  ← the key fix from the architect review
    t = max(0.0, min(1.0, t_raw))

    # Closest point on the segment
    cx = ax + t * abx
    cy = ay + t * aby

    return euclidean_px(px, py, cx, cy), t


# ===========================================================================
# 2.  CSV LOADING  (Layer-1 cache — runs exactly once per session)
# ===========================================================================

@st.cache_data(show_spinner="Loading map data…")
def load_data(csv_path: str = str(_DEFAULT_CSV)) -> pd.DataFrame:
    """
    Load, validate, and enrich the CSV.  Results are cached for the entire
    Streamlit session — the CSV is never parsed more than once.

    Enrichment steps
    ----------------
    * Strip whitespace from string columns.
    * Coerce lat/lng to float; drop rows that cannot be converted.
    * Add synthetic ``id`` column if not present (``row_<index>``).
    * Compute ``px_x`` and ``px_y`` for every row.

    Raises
    ------
    FileNotFoundError  if the CSV path does not exist.
    ValueError         if required columns are missing.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")

    df = pd.read_csv(path)

    # --- Normalise column names (strip whitespace, lowercase) ---------------
    df.columns = [c.strip().lower() for c in df.columns]

    # --- Validate required columns ------------------------------------------
    missing = [c for c in Col.REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    # --- String normalisation -----------------------------------------------
    for col in (Col.NAME, Col.CATEGORY):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # CSV has no 'act' or 'description' columns — skip gracefully
    # (Col.LAT = "x", Col.LNG = "y" per the CSV schema)

    # --- Numeric coercion ---------------------------------------------------
    df[Col.LAT] = pd.to_numeric(df[Col.LAT], errors="coerce")   # x column
    df[Col.LNG] = pd.to_numeric(df[Col.LNG], errors="coerce")   # y column

    bad_rows = df[Col.LAT].isna() | df[Col.LNG].isna()
    if bad_rows.any():
        logger.warning("Dropping %d rows with non-numeric lat/lng.", bad_rows.sum())
        df = df[~bad_rows].copy()

    df = df.reset_index(drop=True)

    # --- Synthetic ID -------------------------------------------------------
    if Col.ID not in df.columns:
        df[Col.ID] = [f"row_{i}" for i in df.index]
    else:
        df[Col.ID] = df[Col.ID].astype(str).str.strip()

    # --- Pixel coordinate computation (vectorised) -------------------------
    # Col.LNG = "y" (horizontal / lng axis) → pixel column
    # Col.LAT = "x" (vertical  / lat axis) → pixel row
    df[Col.PX_X] = (df[Col.LNG] - LNG_SW) / (LNG_NE - LNG_SW) * IMG_W
    df[Col.PX_Y] = (LAT_SW - df[Col.LAT]) / (LAT_SW - LAT_NE) * IMG_H

    logger.info("Loaded %d POI rows from %s.", len(df), path.name)
    return df


# ===========================================================================
# 3.  FILTER LAYER  (Layer-2 cache — cached per unique filter combination)
# ===========================================================================

@st.cache_data(show_spinner=False)
def get_filtered(
    _df_hash: str,
    df: pd.DataFrame,
    category: str = "All",
    search: str = "",
) -> pd.DataFrame:
    """
    Return a filtered view of *df* based on sidebar selections.

    This function is cached: identical (category, search) combos return
    the previously computed slice without re-filtering.

    Parameters
    ----------
    _df_hash : str
        MD5 hash of the full DataFrame — used as part of the cache key so
        the cache is invalidated if source data changes.  Prefixed with ``_``
        so Streamlit does NOT try to hash the DataFrame itself.
    df : pd.DataFrame
        The *full* enriched DataFrame from :func:`load_data`.
    category : str
        Category string (must match CSV values exactly) or ``"All"``.
    search : str
        Free-text search applied against the ``name`` column
        (case-insensitive substring match).

    Returns
    -------
    pd.DataFrame  — a subset of *df* (never a copy of the full frame).
    """
    mask = pd.Series(True, index=df.index)

    if category and category != "All":
        mask &= df[Col.CATEGORY] == category   # exact match (CSV values preserved)

    if search and search.strip():
        q = search.strip().lower()
        mask &= df[Col.NAME].str.lower().str.contains(q, na=False)

    return df[mask].reset_index(drop=True)


def df_hash(df: pd.DataFrame) -> str:
    """
    Produce a stable, cheap hash of a DataFrame for use as a cache-key
    discriminator in :func:`get_filtered`.

    We hash shape + column names + first/last rows to detect structural
    changes without hashing every cell (expensive for 374+ rows).
    """
    sig = f"{df.shape}|{list(df.columns)}|{df.iloc[0].tolist() if len(df) else []}|{df.iloc[-1].tolist() if len(df) else []}"
    return hashlib.md5(sig.encode()).hexdigest()


# ===========================================================================
# 4.  DISTINCT VALUES  (for sidebar filter dropdowns)
# ===========================================================================

def get_categories(df: pd.DataFrame) -> list[str]:
    """Sorted unique category values present in the data (exact CSV strings)."""
    return sorted(df[Col.CATEGORY].dropna().unique().tolist())


# ===========================================================================
# 5.  PROXIMITY ROUTING  (hardened — line-segment projection, t clamped)
# ===========================================================================

def proximity_routing(
    df: pd.DataFrame,
    id_a: str,
    id_b: str,
    n: int = 5,
    corridor_px: float = ROUTE_CORRIDOR_PX,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[list[str], list[tuple[float, float]]]:
    """
    Given two POI IDs, find up to *n* intermediate POIs that lie within
    *corridor_px* pixels of the straight-line segment A→B, sorted by their
    position along that segment (i.e. in travel order).

    Algorithm (architect-review compliant)
    ----------------------------------------
    1. Resolve pixel coordinates for A and B.
    2. Guard against degenerate inputs (same point, missing IDs, empty frame).
    3. For each candidate POI (everything except A and B):
         a. Compute clamped perpendicular distance to segment A→B.
         b. Record parametric position *t* along the segment.
    4. Keep candidates where perp_distance ≤ corridor_px.
    5. Sort survivors by *t* (natural travel order from A to B).
    6. Return the first *n*.

    Parameters
    ----------
    df          : Full or filtered DataFrame (must have px_x, px_y, id cols).
    id_a        : ID of the start POI.
    id_b        : ID of the end POI.
    n           : Maximum number of waypoints to return.
    corridor_px : Half-width of the "corridor" around A→B in pixels.

    Returns
    -------
    (waypoint_ids, all_latlon_coords)
      waypoint_ids    : list[str]             — IDs in travel order.
      all_latlon_coords : list[tuple[float,float]] — full path as (lat,lng)
        tuples: [A, wp1, wp2, …, B].  Use this directly for the Polyline.

    Raises
    ------
    ValueError  if id_a == id_b, or if either ID is not found in *df*.
    """
    # ------------------------------------------------------------------
    # If non-zero offsets are active, recompute pixel coords so routing
    # distances reflect the calibrated frame geometry.
    # ------------------------------------------------------------------
    if offset_x != 0.0 or offset_y != 0.0:
        df = compute_pixels(df, offset_x, offset_y)

    # ------------------------------------------------------------------
    # Guard clauses (architect-review R-03)
    # ------------------------------------------------------------------
    if id_a == id_b:
        raise ValueError("Route requires two distinct POI IDs (id_a == id_b).")

    row_a = df[df[Col.ID] == id_a]
    row_b = df[df[Col.ID] == id_b]

    if row_a.empty:
        raise ValueError(f"Start POI '{id_a}' not found in the DataFrame.")
    if row_b.empty:
        raise ValueError(f"End POI '{id_b}' not found in the DataFrame.")

    a = row_a.iloc[0]
    b = row_b.iloc[0]

    ax, ay = float(a[Col.PX_X]), float(a[Col.PX_Y])
    bx, by = float(b[Col.PX_X]), float(b[Col.PX_Y])

    # ------------------------------------------------------------------
    # Candidates: everything except A and B
    # ------------------------------------------------------------------
    candidates = df[~df[Col.ID].isin([id_a, id_b])].copy()

    if candidates.empty:
        return [], _build_path_coords(df, [], id_a, id_b)

    # Clamp n to however many candidates actually exist
    n = min(n, len(candidates))

    # ------------------------------------------------------------------
    # Vectorised distance computation
    # ------------------------------------------------------------------
    abx, aby = bx - ax, by - ay
    ab_len_sq = abx * abx + aby * aby

    px_arr = candidates[Col.PX_X].to_numpy(dtype=float)
    py_arr = candidates[Col.PX_Y].to_numpy(dtype=float)

    if ab_len_sq < 1e-10:
        # Degenerate segment (A==B in pixel space)
        # Fall back to pure distance from A
        perp_dist = np.hypot(px_arr - ax, py_arr - ay)
        t_arr = np.zeros(len(candidates))
    else:
        # Parametric projection: t = dot(AP, AB) / |AB|²
        apx = px_arr - ax
        apy = py_arr - ay
        t_raw = (apx * abx + apy * aby) / ab_len_sq

        # Clamp t to [0, 1]  ← the critical fix from architect-review R-03
        t_arr = np.clip(t_raw, 0.0, 1.0)

        # Closest point on clamped segment
        cx = ax + t_arr * abx
        cy = ay + t_arr * aby

        perp_dist = np.hypot(px_arr - cx, py_arr - cy)

    candidates = candidates.copy()
    candidates["_perp_dist"] = perp_dist
    candidates["_t"] = t_arr

    # ------------------------------------------------------------------
    # Filter to corridor, sort by travel order
    # ------------------------------------------------------------------
    in_corridor = candidates[candidates["_perp_dist"] <= corridor_px]
    sorted_wps = in_corridor.sort_values("_t").head(n)

    waypoint_ids = sorted_wps[Col.ID].tolist()

    # Build the full lat/lng path: A → wp1 → … → B
    path_coords = _build_path_coords(df, waypoint_ids, id_a, id_b)

    return waypoint_ids, path_coords


def _build_path_coords(
    df: pd.DataFrame,
    waypoint_ids: list[str],
    id_a: str,
    id_b: str,
) -> list[tuple[float, float]]:
    """
    Construct the ordered list of (lat, lng) tuples for the Polyline:
        [A, wp1, wp2, …, B]
    """
    id_order = [id_a, *waypoint_ids, id_b]
    coords: list[tuple[float, float]] = []
    for pid in id_order:
        row = df[df[Col.ID] == pid]
        if not row.empty:
            r = row.iloc[0]
            coords.append((float(r[Col.LAT]), float(r[Col.LNG])))
    return coords


# ===========================================================================
# 6.  SESSION STATE INITIALISATION
# ===========================================================================

def init_state() -> None:
    """
    Idempotently initialise all ``compass.*`` keys in ``st.session_state``.

    Safe to call multiple times (re-runs do not overwrite existing values).
    All mutable defaults (set, list, dict) are created fresh to avoid
    aliasing bugs from shared default objects.
    """
    from constants import (
        DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG, DEFAULT_ZOOM,
        OFFSET_X_DEFAULT, OFFSET_Y_DEFAULT,
    )

    _defaults: dict = {
        SK.COMPLETED:    set(),
        SK.ROUTE_POINTS: [],
        SK.FLY_TO:       None,
        SK.MAP_CENTER:   (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG),
        SK.MAP_ZOOM:     DEFAULT_ZOOM,
        SK.FILTERS:      {"category": "All"},
        SK.SEARCH:       "",
        SK.ROUTE_N:      5,
        SK.OFFSET_X:     OFFSET_X_DEFAULT,
        SK.OFFSET_Y:     OFFSET_Y_DEFAULT,
    }

    for key, default in _defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ===========================================================================
# 7.  STATE HELPERS  (thin wrappers — keep app.py readable)
# ===========================================================================

def mark_completed(item_id: str) -> None:
    """Toggle the completed status of a POI by ID."""
    completed: set = st.session_state[SK.COMPLETED]
    if item_id in completed:
        completed.discard(item_id)
    else:
        completed.add(item_id)


def is_completed(item_id: str) -> bool:
    return item_id in st.session_state.get(SK.COMPLETED, set())


def fly_to_poi(df: pd.DataFrame, item_id: str) -> None:
    """
    Trigger the "center injection" fly-to pattern (architect-review R-02).

    Sets ``compass.map_center`` and ``compass.map_zoom`` so the *next*
    Streamlit rerun rebuilds the Folium map centred on the target POI.
    """
    row = df[df[Col.ID] == item_id]
    if row.empty:
        return
    r = row.iloc[0]
    st.session_state[SK.MAP_CENTER] = (float(r[Col.LAT]), float(r[Col.LNG]))
    st.session_state[SK.MAP_ZOOM] = 5


def set_route_point(item_id: str) -> None:
    """
    Add *item_id* to the route selection (max 2 points).

    Cycle: empty → [A] → [A, B] → [B] → [B, new] → …
    """
    pts: list = st.session_state[SK.ROUTE_POINTS]
    if item_id in pts:
        pts.remove(item_id)
    elif len(pts) < 2:
        pts.append(item_id)
    else:
        # Shift: drop A, keep B, add new as B
        pts[0] = pts[1]
        pts[1] = item_id


def get_route_points() -> list[str]:
    return st.session_state.get(SK.ROUTE_POINTS, [])


def clear_route() -> None:
    st.session_state[SK.ROUTE_POINTS] = []
