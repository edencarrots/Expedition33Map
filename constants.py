"""
constants.py — Expedition 33 Compass
====================================
Single source of truth for all configuration.
No Streamlit imports. No side effects. Pure data.

CSV column reality (expedition33_map_coordinates.csv):
  id, name, category, x, y
  x  → CRS.Simple "lat" axis (vertical, range -260…30)
  y  → CRS.Simple "lng" axis (horizontal, range 20…215)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Final


# ---------------------------------------------------------------------------
# Map Coordinate System (L.CRS.Simple — pixel space)
# ---------------------------------------------------------------------------

#: Latitude bounds of the map image (SW → NE).
#: In L.CRS.Simple: lat maps to the Y-axis (row), lng to the X-axis (col).
LAT_SW: Final[float] = 30.0     # top of coord range  → pixel row 0
LAT_NE: Final[float] = -260.0   # bottom              → pixel row 6828

LNG_SW: Final[float] = 20.0     # left of coord range → pixel col 0
LNG_NE: Final[float] = 215.0    # right               → pixel col 8192

#: Source image dimensions in pixels.
IMG_W: Final[int] = 8192
IMG_H: Final[int] = 6828

# Folium ImageOverlay bounds: [[SW_lat, SW_lng], [NE_lat, NE_lng]]
# NE_lat is numerically smaller (-260) because lat axis is inverted.
IMAGE_BOUNDS: Final[list[list[float]]] = [
    [LAT_NE, LNG_SW],   # bottom-left  → [-260, 20]
    [LAT_SW, LNG_NE],   # top-right    → [30,  215]
]

#: Default map center (rough midpoint of the coordinate range).
DEFAULT_CENTER_LAT: Final[float] = (LAT_SW + LAT_NE) / 2   # -115.0
DEFAULT_CENTER_LNG: Final[float] = (LNG_SW + LNG_NE) / 2   # 117.5
DEFAULT_ZOOM: Final[int] = 2
MIN_ZOOM: Final[int] = 1
MAX_ZOOM: Final[int] = 6


# ---------------------------------------------------------------------------
# Coordinate Offset / Calibration
# ---------------------------------------------------------------------------
# The source image may have a visual frame/border around the active map area.
# OFFSET_X and OFFSET_Y represent that frame width in *pixels*.
#
# Effect on the pixel formula:
#   px_x = OFFSET_X + (lng - LNG_SW) / (LNG_NE - LNG_SW) * (IMG_W - 2*OFFSET_X)
#   px_y = OFFSET_Y + (LAT_SW - lat) / (LAT_SW - LAT_NE) * (IMG_H - 2*OFFSET_Y)
#
# Effect on ImageOverlay bounds:
#   The bounds EXPAND so that the full image (including frame) is placed
#   correctly in CRS.Simple space — see map_component.compute_image_bounds().
#
# At runtime, offset values are stored in st.session_state (SK.OFFSET_X /
# SK.OFFSET_Y) so calibration sliders can adjust them live without reloading.
# These module-level values are the STARTUP DEFAULTS only.
OFFSET_X_DEFAULT: Final[float] = 0.0   # px — horizontal frame width (each side)
OFFSET_Y_DEFAULT: Final[float] = 0.0   # px — vertical   frame width (each side)
OFFSET_MAX:       Final[float] = 500.0  # px — slider upper bound

# ---------------------------------------------------------------------------
# Developer / Calibration Mode
# ---------------------------------------------------------------------------
#: When True, the sidebar shows live OFFSET_X / OFFSET_Y calibration sliders.
#: Set to False (or driven by an env var) to hide them in production.
DEV_MODE: bool = True


# ---------------------------------------------------------------------------
# Session State Keys  (all namespaced under "compass.")
# ---------------------------------------------------------------------------

class SK:
    """Session-state key constants.  Access as SK.COMPLETED, SK.FILTERS, …"""

    COMPLETED:    Final[str] = "compass.completed"       # set[str]   — item IDs
    ROUTE_POINTS: Final[str] = "compass.route_points"   # list[str]  — exactly 0-2 IDs
    FLY_TO:       Final[str] = "compass.fly_to"         # tuple[float,float] | None
    MAP_CENTER:   Final[str] = "compass.map_center"     # tuple[float,float]
    MAP_ZOOM:     Final[str] = "compass.map_zoom"       # int
    FILTERS:      Final[str] = "compass.filters"        # dict
    SEARCH:       Final[str] = "compass.search"         # str
    ROUTE_N:      Final[str] = "compass.route_n"        # int — waypoint count
    OFFSET_X:     Final[str] = "compass.offset_x"       # float — horizontal frame px
    OFFSET_Y:     Final[str] = "compass.offset_y"       # float — vertical   frame px

    #: All keys with their safe default values (used by init_state()).
    DEFAULTS: Final[dict] = {
        "compass.completed":    None,   # built as set() in init_state
        "compass.route_points": None,   # built as list() in init_state
        "compass.fly_to":       None,
        "compass.map_center":   (DEFAULT_CENTER_LAT, DEFAULT_CENTER_LNG),
        "compass.map_zoom":     DEFAULT_ZOOM,
        "compass.filters":      None,   # built as dict in init_state
        "compass.search":       "",
        "compass.route_n":      5,
        # Calibration offsets — start at 0 (no frame assumed)
        "compass.offset_x":     0.0,
        "compass.offset_y":     0.0,
    }


# ---------------------------------------------------------------------------
# CSV / Data Column Names
# ---------------------------------------------------------------------------

class Col:
    """Column name constants for the loaded DataFrame.

    The CSV uses 'x' for the vertical CRS.Simple axis (what Folium calls lat)
    and 'y' for the horizontal axis (what Folium calls lng).  All Folium
    calls therefore use [row[Col.LAT], row[Col.LNG]] = [row['x'], row['y']].
    """

    ID:       Final[str] = "id"
    NAME:     Final[str] = "name"
    CATEGORY: Final[str] = "category"
    LAT:      Final[str] = "x"       # CRS.Simple lat  (vertical axis)
    LNG:      Final[str] = "y"       # CRS.Simple lng  (horizontal axis)
    PX_X:     Final[str] = "px_x"   # computed pixel column  (derived)
    PX_Y:     Final[str] = "px_y"   # computed pixel row     (derived)

    #: Columns that MUST exist in the CSV (before pixel columns are added).
    REQUIRED: Final[tuple[str, ...]] = (NAME, CATEGORY, "x", "y")


# ---------------------------------------------------------------------------
# Category Definitions & Icon Styling
# ---------------------------------------------------------------------------

#: All known categories.  Keys must match values found in the CSV.
#: Each entry defines the Folium marker appearance.
@dataclass(frozen=True)
class CategoryStyle:
    label:        str           # human-readable label shown in sidebar
    color:        str           # Folium marker color
    icon:         str           # Bootstrap / Font-Awesome icon name
    prefix:       str = "fa"    # icon library prefix ("fa" or "glyphicon")
    z_index_offset: int = 0     # stacking order for overlapping markers


# Keys MUST match the exact strings that appear in the CSV 'category' column.
# Font Awesome 4.7 icon names are used (Folium's bundled FA version).
CATEGORY_STYLES: Final[dict[str, CategoryStyle]] = {
    # ── High-priority combat ────────────────────────────────────────────────
    "Bosses": CategoryStyle(
        label="Bosses",
        color="darkred",
        icon="ban",                 # closest to "skull" in FA4
        z_index_offset=1000,
    ),
    "Enemies": CategoryStyle(
        label="Enemies",
        color="red",
        icon="exclamation-triangle",
        z_index_offset=900,
    ),
    # ── Equipment ───────────────────────────────────────────────────────────
    "Weapons": CategoryStyle(
        label="Weapons",
        color="orange",
        icon="bolt",
        z_index_offset=800,
    ),
    "Outfits": CategoryStyle(
        label="Outfits",
        color="cadetblue",
        icon="user",
        z_index_offset=700,
    ),
    "Chroma": CategoryStyle(
        label="Chroma",
        color="purple",
        icon="adjust",
        z_index_offset=650,
    ),
    # ── Collectibles ────────────────────────────────────────────────────────
    "TintsMaterials": CategoryStyle(
        label="Tints & Materials",
        color="lightblue",
        icon="flask",
        z_index_offset=600,
    ),
    "Pictos": CategoryStyle(
        label="Pictos",
        color="pink",
        icon="picture-o",
        z_index_offset=550,
    ),
    "MusicRecords": CategoryStyle(
        label="Music Records",
        color="lightred",
        icon="music",
        z_index_offset=500,
    ),
    # ── Lore / Story ────────────────────────────────────────────────────────
    "ExpeditionJournals": CategoryStyle(
        label="Expedition Journals",
        color="darkblue",
        icon="book",
        z_index_offset=480,
    ),
    # ── NPCs / World ────────────────────────────────────────────────────────
    "LostGestrals": CategoryStyle(
        label="Lost Gestrals",
        color="blue",
        icon="question",
        z_index_offset=450,
    ),
    "Merchants": CategoryStyle(
        label="Merchants",
        color="green",
        icon="shopping-cart",
        z_index_offset=400,
    ),
    "Locations": CategoryStyle(
        label="Locations",
        color="beige",
        icon="map-marker",
        z_index_offset=300,
    ),
    # ── Fallback ────────────────────────────────────────────────────────────
    "_default": CategoryStyle(
        label="Other",
        color="gray",
        icon="info-circle",
        z_index_offset=0,
    ),
}

#: Convenience: sorted list of all user-facing category names (for sidebar filter).
ALL_CATEGORIES: Final[list[str]] = sorted(
    style.label
    for key, style in CATEGORY_STYLES.items()
    if key != "_default"
)


# ---------------------------------------------------------------------------
# Routing Constants
# ---------------------------------------------------------------------------

ROUTE_MAX_WAYPOINTS: Final[int] = 10       # upper cap on N slider
ROUTE_DEFAULT_N:     Final[int] = 5        # default waypoints shown
ROUTE_LINE_COLOR:    Final[str] = "#FF6B35"
ROUTE_LINE_OPACITY:  Final[float] = 0.85
ROUTE_LINE_WEIGHT:   Final[int] = 3
ROUTE_LINE_DASH:     Final[str] = "8 6"    # CSS dash array for dashed polyline

#: Maximum perpendicular pixel distance from the A→B segment to consider
#: a POI as "on the way".  Tune this as needed.
ROUTE_CORRIDOR_PX:   Final[float] = 600.0


# ---------------------------------------------------------------------------
# UI / Display
# ---------------------------------------------------------------------------

APP_TITLE:    Final[str] = "🧭 Compass — Expedition 33 Interactive Map"
APP_ICON:     Final[str] = "🧭"
APP_LAYOUT:   Final[str] = "wide"
SIDEBAR_WIDTH: Final[int] = 360   # approximate CSS px — informational only

COMPLETED_ICON: Final[str] = "✅"
PENDING_ICON:   Final[str] = "⬜"


# ---------------------------------------------------------------------------
# File Paths  (relative to project root — resolved at runtime by each module)
# ---------------------------------------------------------------------------

#: Map background image (AVIF, 8192×6828 px).
MAP_IMAGE_PATH: Final[str] = "assets/map_bg.avif"
MAP_IMAGE_MIME: Final[str] = "image/avif"

#: Source CSV filename (placed at project root).
CSV_FILENAME:   Final[str] = "expedition33_map_coordinates.csv"

# ---------------------------------------------------------------------------
# CSS colour lookup for DivIcon rendering
# Folium's named colours → hex values used in inline HTML/CSS
# ---------------------------------------------------------------------------
FOLIUM_COLOR_HEX: Final[dict[str, str]] = {
    "red":       "#e74c3c",
    "darkred":   "#922b21",
    "orange":    "#e67e22",
    "green":     "#2ecc71",
    "darkgreen": "#1e8449",
    "lightgreen":"#58d68d",
    "blue":      "#3498db",
    "darkblue":  "#1a5276",
    "lightblue": "#85c1e9",
    "purple":    "#9b59b6",
    "cadetblue": "#5dade2",
    "pink":      "#f48fb1",
    "lightred":  "#f1948a",
    "beige":     "#d4ac0d",
    "white":     "#ecf0f1",
    "gray":      "#7f8c8d",
    "black":     "#2c3e50",
}
