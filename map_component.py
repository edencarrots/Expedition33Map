"""
map_component.py — Expedition 33 Compass
=========================================
Pure Folium layer.  No st.session_state writes.  No business logic.

Contract
--------
  build_map(...) → folium.Map
    Caller passes the result directly to st_folium().
    All state decisions are made *before* this call (in app.py).

Caching strategy (architect-review R-01)
-----------------------------------------
  Layer 3 — _encode_image()  : @st.cache_data — AVIF encoded once per session.
  The map object itself is NOT cached here; app.py caches build_map via
  a hash of the filtered DataFrame + state flags.  Folium objects are not
  reliably hashable so we let app.py decide the cache boundary.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import folium
import folium.plugins
import pandas as pd
import streamlit as st

from constants import (
    APP_TITLE,
    CATEGORY_STYLES,
    Col,
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LNG,
    DEFAULT_ZOOM,
    FOLIUM_COLOR_HEX,
    IMAGE_BOUNDS,
    IMG_H,
    IMG_W,
    LAT_NE,
    LAT_SW,
    LNG_NE,
    LNG_SW,
    MAP_IMAGE_MIME,
    MAP_IMAGE_PATH,
    MAX_ZOOM,
    MIN_ZOOM,
    ROUTE_LINE_COLOR,
    ROUTE_LINE_DASH,
    ROUTE_LINE_OPACITY,
    ROUTE_LINE_WEIGHT,
)

# ---------------------------------------------------------------------------
# Resolved asset path (relative to this module's directory)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent
_DEFAULT_IMG_PATH = _PROJECT_ROOT / MAP_IMAGE_PATH


# ===========================================================================
# COORDINATE OFFSET — ImageOverlay bounds computation
# ===========================================================================

def compute_image_bounds(
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[list[float]]:
    """
    Compute the Folium ImageOverlay ``bounds`` that correctly positions the
    full image (including any visual frame/border) in CRS.Simple space.

    Background
    ----------
    In CRS.Simple the ImageOverlay ``bounds`` = [[SW_lat, SW_lng], [NE_lat, NE_lng]]
    define where the *entire image file* is placed on the map.  When the image
    has a frame of ``offset_x`` pixels on each horizontal side and ``offset_y``
    pixels on each vertical side, the *active map content* occupies the inner
    pixel rectangle [offset_x : IMG_W-offset_x, offset_y : IMG_H-offset_y].

    We want the data coordinate range ([LAT_NE, LNG_SW] → [LAT_SW, LNG_NE])
    to map to that inner rectangle.  To achieve this we must EXPAND the overlay
    bounds beyond the data range — the frame area maps to "extra" coordinates
    outside the data range.

    Derivation
    ----------
    From the offset pixel formula:
        px_x = offset_x + (lng - LNG_SW) / (LNG_NE - LNG_SW) * (IMG_W - 2*offset_x)

    Solving for ``lng`` when px_x = 0  (left image edge):
        lng_left  = LNG_SW - offset_x  * (LNG_NE - LNG_SW) / (IMG_W - 2*offset_x)

    Solving for ``lng`` when px_x = IMG_W  (right image edge):
        lng_right = LNG_SW + (IMG_W - offset_x) * (LNG_NE - LNG_SW) / (IMG_W - 2*offset_x)

    Analogously for lat (note: lat=LAT_SW maps to top/row-0, lat=LAT_NE to bottom):
        lat_top   = LAT_SW + offset_y  * (LAT_SW - LAT_NE) / (IMG_H - 2*offset_y)
        lat_bot   = LAT_SW - (IMG_H - offset_y) * (LAT_SW - LAT_NE) / (IMG_H - 2*offset_y)

    When offset_x = offset_y = 0 the result is the original IMAGE_BOUNDS.

    Returns
    -------
    [[lat_bot, lng_left], [lat_top, lng_right]]
      i.e.  [[SW_lat, SW_lng], [NE_lat, NE_lng]]  in Folium convention.
    """
    if offset_x == 0.0 and offset_y == 0.0:
        return IMAGE_BOUNDS   # fast path — no allocation

    lng_range = LNG_NE - LNG_SW
    lat_range = LAT_SW - LAT_NE   # positive: LAT_SW=30 > LAT_NE=-260

    active_w = IMG_W - 2.0 * offset_x
    active_h = IMG_H - 2.0 * offset_y

    # Guard: avoid division by zero if someone sets offset to IMG/2
    if active_w <= 0 or active_h <= 0:
        return IMAGE_BOUNDS

    lng_left  = LNG_SW - offset_x * lng_range / active_w
    lng_right = LNG_SW + (IMG_W - offset_x) * lng_range / active_w

    lat_top   = LAT_SW + offset_y * lat_range / active_h
    lat_bot   = LAT_SW - (IMG_H - offset_y) * lat_range / active_h

    return [[lat_bot, lng_left], [lat_top, lng_right]]


# ===========================================================================
# LAYER 3 CACHE — Image encoding  (runs exactly once per session)
# ===========================================================================

@st.cache_data(show_spinner="Loading map image…")
def _encode_image(img_path: str) -> str:
    """
    Read the AVIF (or PNG) file and return a base64 data URI.

    Cached with @st.cache_data so the ~8 MB encoding is computed only once
    per Streamlit session regardless of how many times build_map() is called.

    Parameters
    ----------
    img_path : str   Absolute path to the image file.

    Returns
    -------
    str  "data:image/avif;base64,<b64_content>"
    """
    path = Path(img_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Map image not found: {path.resolve()}\n"
            f"Expected at: {_DEFAULT_IMG_PATH}"
        )
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("utf-8")

    mime = MAP_IMAGE_MIME if path.suffix == ".avif" else "image/png"
    return f"data:{mime};base64,{b64}"


# ===========================================================================
# ICON FACTORY
# ===========================================================================

def _hex_color(folium_color: str) -> str:
    """Resolve a Folium color name to a CSS hex string."""
    return FOLIUM_COLOR_HEX.get(folium_color, folium_color)


def _make_poi_icon(
    category: str,
    is_completed: bool = False,
    is_waypoint: bool = False,
    is_endpoint: bool = False,
) -> folium.DivIcon:
    """
    Build a CSS-styled HTML DivIcon for a POI marker.

    Priority: endpoint > waypoint > category-specific.

    Visual language
    ---------------
    Endpoint  — larger pulsing circle, route-orange border glow.
    Waypoint  — small rotated diamond, amber fill.
    Completed — muted opacity (0.45) + small green ✅ badge.
    Default   — filled circle with category colour + FA icon.
    """
    if is_endpoint:
        return _endpoint_icon(is_completed)
    if is_waypoint:
        return _waypoint_icon()
    return _category_icon(category, is_completed)


def _category_icon(category: str, is_completed: bool) -> folium.DivIcon:
    style = CATEGORY_STYLES.get(category, CATEGORY_STYLES["_default"])
    color = _hex_color(style.color)
    opacity = "0.40" if is_completed else "1.0"
    border = "#27ae60" if is_completed else color
    badge = (
        '<span style="'
        "position:absolute;top:-5px;right:-5px;"
        "font-size:8px;line-height:1;background:white;"
        'border-radius:50%;padding:1px;">'
        "✅</span>"
        if is_completed
        else ""
    )
    html = (
        f'<div style="'
        f"position:relative;"
        f"width:24px;height:24px;"
        f"background:{color};"
        f"border:2px solid {border};"
        f"border-radius:50%;"
        f"opacity:{opacity};"
        f"display:flex;align-items:center;justify-content:center;"
        f'box-shadow:0 1px 4px rgba(0,0,0,0.45);">'
        f'<i class="fa fa-{style.icon}" '
        f'style="color:white;font-size:10px;"></i>'
        f"{badge}"
        f"</div>"
    )
    return folium.DivIcon(
        html=html,
        icon_size=(24, 24),
        icon_anchor=(12, 12),
        popup_anchor=(0, -14),
        class_name="compass-poi-icon",
    )


def _endpoint_icon(is_completed: bool) -> folium.DivIcon:
    color = "#27ae60" if is_completed else ROUTE_LINE_COLOR
    html = (
        f'<div style="'
        f"width:30px;height:30px;"
        f"background:{color};"
        f"border:3px solid white;"
        f"border-radius:50%;"
        f"box-shadow:0 0 10px {color},0 0 20px {color}55;"
        f"display:flex;align-items:center;justify-content:center;"
        f'">'
        f'<i class="fa fa-location-arrow" '
        f'style="color:white;font-size:13px;"></i>'
        f"</div>"
    )
    return folium.DivIcon(
        html=html,
        icon_size=(30, 30),
        icon_anchor=(15, 15),
        popup_anchor=(0, -17),
        class_name="compass-endpoint-icon",
    )


def _waypoint_icon() -> folium.DivIcon:
    html = (
        '<div style="'
        "width:14px;height:14px;"
        "background:#f39c12;"
        "border:2px solid white;"
        "transform:rotate(45deg);"
        'box-shadow:0 1px 4px rgba(0,0,0,0.4);">'
        "</div>"
    )
    return folium.DivIcon(
        html=html,
        icon_size=(14, 14),
        icon_anchor=(7, 7),
        popup_anchor=(0, -9),
        class_name="compass-waypoint-icon",
    )


# ===========================================================================
# POPUP FACTORY
# ===========================================================================

def _make_popup(row: pd.Series, is_completed: bool) -> folium.Popup:
    """Styled HTML popup shown when a marker is clicked."""
    style = CATEGORY_STYLES.get(str(row[Col.CATEGORY]), CATEGORY_STYLES["_default"])
    color = _hex_color(style.color)
    status_html = (
        '<span style="color:#27ae60;font-weight:700;">✅ Completed</span>'
        if is_completed
        else '<span style="color:#7f8c8d;">⬜ Not visited</span>'
    )
    # Show pixel coords for debugging — remove if noisy
    html = (
        '<div style="font-family:\'Segoe UI\',sans-serif;'
        'min-width:170px;max-width:230px;padding:2px;">'
        f'<div style="font-weight:700;font-size:13px;'
        f'color:#1a1a2e;margin-bottom:5px;">{row[Col.NAME]}</div>'
        f'<div style="margin-bottom:6px;">'
        f'<span style="background:{color};color:white;'
        f'padding:2px 8px;border-radius:12px;font-size:10px;">'
        f"{style.label}</span></div>"
        f'<div style="font-size:11px;margin-bottom:4px;">{status_html}</div>'
        f'<div style="font-size:10px;color:#aaa;">'
        f"ID: {row[Col.ID]}</div>"
        f"</div>"
    )
    return folium.Popup(html, max_width=250)


# ===========================================================================
# MAIN BUILD FUNCTION
# ===========================================================================

def build_map(
    df: pd.DataFrame,
    completed_ids: set,
    route_points: list[str],
    fly_to: Optional[tuple[float, float]],
    map_center: tuple[float, float],
    map_zoom: int,
    route_coords: Optional[list[tuple[float, float]]] = None,
    waypoint_ids: Optional[list[str]] = None,
    img_path: str = str(_DEFAULT_IMG_PATH),
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> folium.Map:
    """
    Build and return a fully configured Folium map.

    The returned object is passed directly to ``st_folium()`` in app.py.
    This function has NO side-effects on session_state.

    Center Injection (architect-review R-02)
    -----------------------------------------
    ``fly_to`` takes priority over ``map_center``.  Both come from
    session_state and are resolved by app.py before calling this function.
    Setting ``map_center`` in session_state and triggering a Streamlit rerun
    is the mechanism that achieves "fly-to" behaviour.

    Offset / Calibration
    ---------------------
    ``offset_x`` and ``offset_y`` are frame widths in pixels.  They expand
    the ImageOverlay bounds via :func:`compute_image_bounds` so the image's
    visual frame/border lands *outside* the data coordinate range and markers
    align with the actual map content inside the frame.

    Parameters
    ----------
    df            : Filtered DataFrame (from data_engine.get_filtered).
    completed_ids : Set of POI IDs the user has checked off.
    route_points  : List of 0–2 POI IDs selected as route endpoints.
    fly_to        : Override center → (lat, lng) or None.
    map_center    : Persisted center from session_state.
    map_zoom      : Persisted zoom  from session_state.
    route_coords  : Full [(lat,lng), …] path from proximity_routing().
    waypoint_ids  : Intermediate waypoint IDs (rendered distinctly).
    img_path      : Absolute path to the AVIF map file.
    offset_x      : Horizontal frame offset in pixels (default 0).
    offset_y      : Vertical   frame offset in pixels (default 0).
    """
    # ------------------------------------------------------------------
    # R-02: Center injection — fly_to overrides persisted center
    # ------------------------------------------------------------------
    center = list(fly_to) if fly_to is not None else list(map_center)

    # ------------------------------------------------------------------
    # Compute offset-adjusted ImageOverlay bounds
    # ------------------------------------------------------------------
    overlay_bounds = compute_image_bounds(offset_x, offset_y)

    # ------------------------------------------------------------------
    # Initialise Folium map with CRS.Simple
    # ------------------------------------------------------------------
    m = folium.Map(
        location=center,
        zoom_start=map_zoom,
        crs="Simple",
        tiles=None,              # No tile background — we supply the image
        min_zoom=MIN_ZOOM,
        max_zoom=MAX_ZOOM,
        zoom_control=True,
        scrollWheelZoom=True,
        attributionControl=False,
    )

    # ------------------------------------------------------------------
    # 8K Image Overlay  (Layer-3 cache: image encoded once per session)
    # ------------------------------------------------------------------
    img_data_uri = _encode_image(img_path)
    folium.raster_layers.ImageOverlay(
        image=img_data_uri,
        bounds=overlay_bounds,   # Offset-expanded bounds (or IMAGE_BOUNDS if 0)
        opacity=1.0,
        name="Expedition 33 Map",
        interactive=False,      # don't intercept clicks
        cross_origin=False,
        zindex=1,
    ).add_to(m)

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------
    waypoint_set = set(waypoint_ids or [])
    route_set = set(route_points or [])

    for _, row in df.iterrows():
        pid = str(row[Col.ID])
        cat = str(row[Col.CATEGORY])
        completed = pid in completed_ids
        is_waypoint = pid in waypoint_set
        is_endpoint = pid in route_set

        icon = _make_poi_icon(
            category=cat,
            is_completed=completed,
            is_waypoint=is_waypoint,
            is_endpoint=is_endpoint,
        )

        style = CATEGORY_STYLES.get(cat, CATEGORY_STYLES["_default"])

        folium.Marker(
            # location = [lat, lng] in CRS.Simple space
            # Col.LAT = "x" column, Col.LNG = "y" column
            location=[float(row[Col.LAT]), float(row[Col.LNG])],
            icon=icon,
            popup=_make_popup(row, completed),
            tooltip=f"<b>{row[Col.NAME]}</b><br><i>{style.label}</i>",
            z_index_offset=style.z_index_offset,
        ).add_to(m)

    # ------------------------------------------------------------------
    # Route Polyline  (only drawn when 2 endpoints are selected)
    # ------------------------------------------------------------------
    if route_coords and len(route_coords) >= 2:
        # Dashed path: A → [waypoints] → B
        folium.PolyLine(
            locations=[[lat, lng] for lat, lng in route_coords],
            color=ROUTE_LINE_COLOR,
            weight=ROUTE_LINE_WEIGHT,
            opacity=ROUTE_LINE_OPACITY,
            dash_array=ROUTE_LINE_DASH,
            tooltip="🧭 Smart Route",
        ).add_to(m)

        # Start marker — glowing circle
        folium.CircleMarker(
            location=list(route_coords[0]),
            radius=12,
            color=ROUTE_LINE_COLOR,
            fill=True,
            fill_color=ROUTE_LINE_COLOR,
            fill_opacity=0.25,
            weight=3,
            tooltip="▶ Route Start",
        ).add_to(m)

        # End marker — green circle
        folium.CircleMarker(
            location=list(route_coords[-1]),
            radius=12,
            color="#27ae60",
            fill=True,
            fill_color="#27ae60",
            fill_opacity=0.25,
            weight=3,
            tooltip="⬛ Route End",
        ).add_to(m)

    return m


# ===========================================================================
# STANDALONE SMOKE TEST
# ===========================================================================

if __name__ == "__main__":
    """
    Quick smoke test — run directly with:
        python map_component.py
    Verifies the map builds without exceptions and writes a test HTML file.
    """
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent))

    import pandas as pd
    from data_engine import load_data, get_filtered, df_hash, init_state

    # Monkey-patch st.cache_data for non-Streamlit context
    import streamlit as _st
    _st.cache_data = lambda *a, **kw: (lambda f: f)

    csv_path = str(_PROJECT_ROOT / "expedition33_map_coordinates.csv")
    df_full = pd.read_csv(csv_path)
    df_full.columns = [c.strip().lower() for c in df_full.columns]
    from constants import LNG_SW, LNG_NE, LAT_SW, LAT_NE, IMG_W, IMG_H
    df_full[Col.LAT] = pd.to_numeric(df_full[Col.LAT], errors="coerce")
    df_full[Col.LNG] = pd.to_numeric(df_full[Col.LNG], errors="coerce")
    df_full[Col.PX_X] = (df_full[Col.LNG] - LNG_SW) / (LNG_NE - LNG_SW) * IMG_W
    df_full[Col.PX_Y] = (LAT_SW - df_full[Col.LAT]) / (LAT_SW - LAT_NE) * IMG_H
    df_full[Col.ID] = df_full[Col.ID].astype(str)

    m = build_map(
        df=df_full,
        completed_ids=set(),
        route_points=[],
        fly_to=None,
        map_center=(DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG),
        map_zoom=DEFAULT_ZOOM,
    )
    out = _PROJECT_ROOT / "smoke_test_map.html"
    m.save(str(out))
    print(f"Smoke test passed — map saved to {out}")
