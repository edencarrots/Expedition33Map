"""
app.py — Expedition 33 Compass
================================
UI Orchestrator.  The ONLY file allowed to call `st.*` directly.

Rerun lifecycle
---------------
Every widget interaction triggers a full top-to-bottom rerun.
This file is designed so that:
  1. Cached data (CSV, filtered frame, image) is never recomputed.
  2. Only the Folium map object is rebuilt (cheap once image is cached).
  3. State mutations always happen *before* the map render block.

Fly-to pattern (architect-review R-02)
----------------------------------------
Clicking "Focus" on a checklist item:
  → sets SK.MAP_CENTER + SK.MAP_ZOOM + SK.MAP_KEY in session_state
  → triggers st.rerun()
  → next rerun: build_map() receives the new center → map re-renders centred.
"""
from __future__ import annotations

import streamlit as st
from streamlit_folium import st_folium

import data_engine as de
import map_component as mc
from constants import (
    ALL_CATEGORIES,
    APP_ICON,
    APP_LAYOUT,
    APP_TITLE,
    CATEGORY_STYLES,
    Col,
    COMPLETED_ICON,
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LNG,
    DEFAULT_ZOOM,
    DEV_MODE,
    FOLIUM_COLOR_HEX,
    OFFSET_MAX,
    OFFSET_X_DEFAULT,
    OFFSET_Y_DEFAULT,
    PENDING_ICON,
    ROUTE_DEFAULT_N,
    ROUTE_MAX_WAYPOINTS,
    SK,
)

# ===========================================================================
# PAGE CONFIG  (must be the first Streamlit call)
# ===========================================================================
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout=APP_LAYOUT,
    initial_sidebar_state="expanded",
)


# ===========================================================================
# DARK FANTASY CSS INJECTION
# ===========================================================================
st.markdown(
    """
    <style>
    /* ── Global ─────────────────────────────────────────────────────────── */
    html, body, [data-testid="stApp"] {
        background-color: #0f0f14;
        color: #e8dcc8;
        font-family: 'Segoe UI', 'Georgia', serif;
    }

    /* ── Sidebar ─────────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background-color: #13131a !important;
        border-right: 1px solid #2a2a3a;
    }
    [data-testid="stSidebar"] * { color: #e8dcc8 !important; }

    /* ── Sidebar header strip ────────────────────────────────────────────── */
    .compass-sidebar-title {
        font-size: 1.35rem;
        font-weight: 700;
        color: #ffbf00 !important;
        letter-spacing: 0.04em;
        padding: 0.5rem 0 0.25rem;
        border-bottom: 1px solid #2a2a3a;
        margin-bottom: 0.75rem;
    }
    .compass-section-label {
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #ffbf00 !important;
        margin: 1.1rem 0 0.35rem;
    }

    /* ── Inputs / Selects ───────────────────────────────────────────────── */
    .stTextInput input, .stMultiSelect > div,
    .stSelectbox > div > div, .stNumberInput input {
        background-color: #1e1e2e !important;
        border: 1px solid #2e2e4a !important;
        color: #e8dcc8 !important;
        border-radius: 6px;
    }
    .stTextInput input:focus {
        border-color: #ffbf00 !important;
        box-shadow: 0 0 0 2px #ffbf0033 !important;
    }

    /* ── Buttons ─────────────────────────────────────────────────────────── */
    .stButton > button {
        background: #1e1e2e;
        color: #ffbf00;
        border: 1px solid #ffbf0055;
        border-radius: 5px;
        font-size: 0.78rem;
        padding: 0.2rem 0.6rem;
        transition: all 0.15s ease;
    }
    .stButton > button:hover {
        background: #ffbf00 !important;
        color: #0f0f14 !important;
        border-color: #ffbf00 !important;
    }

    /* ── Checklist rows ──────────────────────────────────────────────────── */
    .poi-row {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.28rem 0.4rem;
        border-radius: 5px;
        border-bottom: 1px solid #1e1e2e;
        transition: background 0.1s;
    }
    .poi-row:hover { background: #1e1e2e; }
    .poi-name {
        flex: 1;
        font-size: 0.82rem;
        color: #c9bfa8;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .poi-name.done { color: #556b2f; text-decoration: line-through; }
    .poi-cat-badge {
        font-size: 0.65rem;
        padding: 1px 6px;
        border-radius: 8px;
        background: #2a2a3a;
        color: #ffbf00;
        white-space: nowrap;
    }

    /* ── Progress bar ────────────────────────────────────────────────────── */
    .stProgress > div > div {
        background: linear-gradient(90deg, #ffbf00, #ff8c00) !important;
    }

    /* ── Map container ───────────────────────────────────────────────────── */
    .map-wrapper {
        border: 1px solid #2a2a3a;
        border-radius: 8px;
        overflow: hidden;
    }
    iframe { border-radius: 8px; }

    /* ── Stats chips ─────────────────────────────────────────────────────── */
    .stat-chip {
        display: inline-block;
        background: #1e1e2e;
        border: 1px solid #2e2e4a;
        border-radius: 16px;
        padding: 0.2rem 0.75rem;
        font-size: 0.8rem;
        color: #e8dcc8;
        margin-right: 0.4rem;
    }
    .stat-chip span { color: #ffbf00; font-weight: 700; }

    /* ── Route planner box ───────────────────────────────────────────────── */
    .route-box {
        background: #13131a;
        border: 1px solid #2a2a3a;
        border-radius: 8px;
        padding: 0.75rem;
        margin-top: 0.5rem;
    }

    /* ── Scrollable checklist ────────────────────────────────────────────── */
    .checklist-scroll {
        max-height: 340px;
        overflow-y: auto;
        padding-right: 4px;
    }
    .checklist-scroll::-webkit-scrollbar { width: 4px; }
    .checklist-scroll::-webkit-scrollbar-track { background: #0f0f14; }
    .checklist-scroll::-webkit-scrollbar-thumb {
        background: #2e2e4a;
        border-radius: 2px;
    }

    /* ── Hide Streamlit chrome ───────────────────────────────────────────── */
    #MainMenu, footer, header { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ===========================================================================
# BOOTSTRAP
# ===========================================================================
de.init_state()

# Ensure map_key exists for forced refresh
if "compass.map_key" not in st.session_state:
    st.session_state["compass.map_key"] = 0

# Load data (Layer-1 cache — runs once)
df_full = de.load_data()
_hash = de.df_hash(df_full)
all_cats = de.get_categories(df_full)


# ===========================================================================
# SIDEBAR
# ===========================================================================
with st.sidebar:
    st.markdown(
        '<div class="compass-sidebar-title">🧭 Compass</div>'
        '<div style="font-size:0.72rem;color:#7a6e5a;margin-bottom:0.5rem;">'
        "Expedition 33 Interactive Map</div>",
        unsafe_allow_html=True,
    )

    # ── Search ────────────────────────────────────────────────────────────
    st.markdown('<div class="compass-section-label">🔍 Search</div>', unsafe_allow_html=True)
    search_val = st.text_input(
        label="Search POIs",
        value=st.session_state[SK.SEARCH],
        placeholder="Type a name…",
        label_visibility="collapsed",
        key="widget_search",
    )
    st.session_state[SK.SEARCH] = search_val

    # ── Category filter ───────────────────────────────────────────────────
    st.markdown('<div class="compass-section-label">📂 Category</div>', unsafe_allow_html=True)

    cat_options = ["All"] + all_cats
    current_cat = st.session_state[SK.FILTERS].get("category", "All")
    # Guard: if stored value no longer valid, reset
    if current_cat not in cat_options:
        current_cat = "All"

    selected_cat = st.selectbox(
        label="Category",
        options=cat_options,
        index=cat_options.index(current_cat),
        label_visibility="collapsed",
        key="widget_cat",
    )
    st.session_state[SK.FILTERS]["category"] = selected_cat

    # ── Apply filters ─────────────────────────────────────────────────────
    category_arg = selected_cat if selected_cat != "All" else "All"
    df_filtered = de.get_filtered(
        _df_hash=_hash,
        df=df_full,
        category=category_arg,
        search=search_val,
    )

    # ── Progress stats ─────────────────────────────────────────────────────
    total_visible = len(df_filtered)
    total_all = len(df_full)
    done_all = len(st.session_state[SK.COMPLETED])
    done_visible = sum(
        1 for pid in df_filtered[Col.ID].tolist()
        if de.is_completed(pid)
    )

    st.markdown(
        f'<div style="margin:0.6rem 0;">'
        f'<span class="stat-chip">Visible <span>{total_visible}</span></span>'
        f'<span class="stat-chip">Done <span>{done_all}/{total_all}</span></span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if total_all > 0:
        st.progress(done_all / total_all)

    # ── Checklist ─────────────────────────────────────────────────────────
    st.markdown('<div class="compass-section-label">📋 POI Checklist</div>', unsafe_allow_html=True)

    if total_visible == 0:
        st.caption("No POIs match the current filters.")
    else:
        # Scrollable container via HTML wrapper + individual Streamlit widgets
        # We render rows in groups of columns: [checkbox | name+badge | focus btn]
        checklist_items = df_filtered[[Col.ID, Col.NAME, Col.CATEGORY]].values.tolist()

        # Limit display to 80 items in checklist to avoid sidebar overload.
        # Users can use search/filter to narrow further.
        display_items = checklist_items[:80]
        if len(checklist_items) > 80:
            st.caption(f"Showing 80 of {len(checklist_items)} — use search to narrow.")

        for pid, pname, pcat in display_items:
            pid_str = str(pid)
            completed = de.is_completed(pid_str)
            style = CATEGORY_STYLES.get(pcat, CATEGORY_STYLES["_default"])

            col_chk, col_name, col_btn = st.columns([0.12, 0.68, 0.20])

            with col_chk:
                checked = st.checkbox(
                    label="",
                    value=completed,
                    key=f"chk_{pid_str}",
                    label_visibility="collapsed",
                )
                # Toggle if state changed
                if checked != completed:
                    de.mark_completed(pid_str)
                    st.rerun()

            with col_name:
                name_class = "poi-name done" if completed else "poi-name"
                st.markdown(
                    f'<div class="{name_class}" title="{pname}">'
                    f"{COMPLETED_ICON if completed else PENDING_ICON} "
                    f"<b>{pname}</b> "
                    f'<span class="poi-cat-badge">{style.label}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with col_btn:
                if st.button("🎯", key=f"fly_{pid_str}", help=f"Focus map on {pname}"):
                    de.fly_to_poi(df_full, pid_str)
                    # Increment map_key to force st_folium to re-mount
                    st.session_state["compass.map_key"] += 1
                    st.rerun()

    # ── Route Planner ─────────────────────────────────────────────────────
    st.markdown('<div class="compass-section-label">🗺️ Route Planner</div>', unsafe_allow_html=True)

    with st.container():
        # Build option list from full dataset for route endpoints
        poi_options = {
            f"{row[Col.NAME]} ({row[Col.CATEGORY]})": str(row[Col.ID])
            for _, row in df_full.iterrows()
        }
        poi_labels = ["— select —"] + list(poi_options.keys())

        start_label = st.selectbox(
            "Start Point",
            options=poi_labels,
            index=0,
            key="widget_route_start",
            label_visibility="visible",
        )
        end_label = st.selectbox(
            "End Point",
            options=poi_labels,
            index=0,
            key="widget_route_end",
            label_visibility="visible",
        )

        route_n = st.slider(
            "Max waypoints",
            min_value=1,
            max_value=ROUTE_MAX_WAYPOINTS,
            value=st.session_state[SK.ROUTE_N],
            key="widget_route_n",
        )
        st.session_state[SK.ROUTE_N] = route_n

        plan_col, clear_col = st.columns(2)
        with plan_col:
            plan_clicked = st.button("⚔️ Plan Route", use_container_width=True)
        with clear_col:
            clear_clicked = st.button("✕ Clear", use_container_width=True)

        if clear_clicked:
            de.clear_route()
            st.session_state["compass.route_coords"] = None
            st.session_state["compass.waypoint_ids"] = []
            st.rerun()

        if plan_clicked:
            id_a = poi_options.get(start_label)
            id_b = poi_options.get(end_label)

            if not id_a or not id_b:
                st.warning("Please select both a Start and End point.")
            elif id_a == id_b:
                st.warning("Start and End must be different POIs.")
            else:
                try:
                    wps, coords = de.proximity_routing(
                        df=df_full,
                        id_a=id_a,
                        id_b=id_b,
                        n=route_n,
                        offset_x=float(st.session_state.get(SK.OFFSET_X, OFFSET_X_DEFAULT)),
                        offset_y=float(st.session_state.get(SK.OFFSET_Y, OFFSET_Y_DEFAULT)),
                    )
                    st.session_state[SK.ROUTE_POINTS] = [id_a, id_b]
                    st.session_state["compass.route_coords"] = coords
                    st.session_state["compass.waypoint_ids"] = wps
                    st.session_state["compass.map_key"] += 1

                    if wps:
                        st.success(f"Route planned — {len(wps)} waypoint(s) found.")
                    else:
                        st.info("Direct route — no waypoints within corridor.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        # Show active route summary
        active_pts = de.get_route_points()
        if len(active_pts) == 2:
            r_coords = st.session_state.get("compass.route_coords") or []
            wps = st.session_state.get("compass.waypoint_ids") or []
            ra = df_full[df_full[Col.ID] == active_pts[0]][Col.NAME].values
            rb = df_full[df_full[Col.ID] == active_pts[1]][Col.NAME].values
            if len(ra) and len(rb):
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#ffbf00;margin-top:0.4rem;">'
                    f"▶ {ra[0]}<br>⬛ {rb[0]}<br>"
                    f'<span style="color:#7a6e5a;">{len(wps)} waypoint(s) • '
                    f"{len(r_coords)} coords</span></div>",
                    unsafe_allow_html=True,
                )

    # ── Legend ────────────────────────────────────────────────────────────
    with st.expander("🎨 Legend", expanded=False):
        for key, style in CATEGORY_STYLES.items():
            if key == "_default":
                continue
            color = FOLIUM_COLOR_HEX.get(style.color, "#888")
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'padding:3px 0;font-size:0.8rem;">'
                f'<span style="width:12px;height:12px;border-radius:50%;'
                f'background:{color};display:inline-block;flex-shrink:0;"></span>'
                f"{style.label}</div>",
                unsafe_allow_html=True,
            )

    # ── 🛠 Calibration Panel (DEV_MODE only) ──────────────────────────────
    if DEV_MODE:
        with st.expander("🛠 Calibration", expanded=False):
            st.markdown(
                '<div style="font-size:0.72rem;color:#7a6e5a;margin-bottom:0.6rem;">'
                "Adjust OFFSET_X / OFFSET_Y to align markers with the visual map "
                "content when the image has a frame or border. "
                "Once aligned, set those values as <code>OFFSET_X_DEFAULT</code> / "
                "<code>OFFSET_Y_DEFAULT</code> in <code>constants.py</code> and "
                "disable <code>DEV_MODE</code>."
                "</div>",
                unsafe_allow_html=True,
            )

            # Current values from session state
            cur_ox = float(st.session_state.get(SK.OFFSET_X, OFFSET_X_DEFAULT))
            cur_oy = float(st.session_state.get(SK.OFFSET_Y, OFFSET_Y_DEFAULT))

            new_ox = st.slider(
                "OFFSET_X  (horizontal frame px)",
                min_value=0.0,
                max_value=float(OFFSET_MAX),
                value=cur_ox,
                step=1.0,
                key="widget_offset_x",
                help="Pixels cropped from each horizontal edge of the image. "
                     "Increase until markers align with left/right map edges.",
            )
            new_oy = st.slider(
                "OFFSET_Y  (vertical frame px)",
                min_value=0.0,
                max_value=float(OFFSET_MAX),
                value=cur_oy,
                step=1.0,
                key="widget_offset_y",
                help="Pixels cropped from each vertical edge of the image. "
                     "Increase until markers align with top/bottom map edges.",
            )

            # Live readout of the computed bounds
            from map_component import compute_image_bounds
            preview_bounds = compute_image_bounds(new_ox, new_oy)
            sw, ne = preview_bounds
            st.markdown(
                f'<div style="font-size:0.7rem;color:#7a6e5a;'
                f'font-family:monospace;margin-top:0.4rem;">'
                f"ImageOverlay bounds preview:<br>"
                f"SW [{sw[0]:.2f}, {sw[1]:.2f}] → "
                f"NE [{ne[0]:.2f}, {ne[1]:.2f}]</div>",
                unsafe_allow_html=True,
            )

            # Apply button — only triggers rerun when values actually change
            if new_ox != cur_ox or new_oy != cur_oy:
                st.session_state[SK.OFFSET_X] = new_ox
                st.session_state[SK.OFFSET_Y] = new_oy
                st.session_state["compass.map_key"] += 1
                st.rerun()

            # Reset to defaults
            if st.button("↩ Reset offsets to 0", key="btn_reset_offsets"):
                st.session_state[SK.OFFSET_X] = 0.0
                st.session_state[SK.OFFSET_Y] = 0.0
                st.session_state["compass.map_key"] += 1
                st.rerun()

            st.caption(
                f"Active: OFFSET_X={cur_ox:.0f}px  OFFSET_Y={cur_oy:.0f}px"
            )


# ===========================================================================
# MAIN PANEL
# ===========================================================================

# ── App header ─────────────────────────────────────────────────────────────
st.markdown(
    f'<div style="display:flex;align-items:baseline;gap:0.6rem;'
    f'margin-bottom:0.5rem;">'
    f'<h2 style="color:#ffbf00;margin:0;font-size:1.4rem;'
    f'letter-spacing:0.03em;">{APP_TITLE}</h2>'
    f'<span style="font-size:0.75rem;color:#55504a;">'
    f"{total_visible} POIs shown · {done_all}/{total_all} completed</span>"
    f"</div>",
    unsafe_allow_html=True,
)

# ── Resolve map state ───────────────────────────────────────────────────────
map_center   = st.session_state[SK.MAP_CENTER]
map_zoom     = st.session_state[SK.MAP_ZOOM]
fly_to       = st.session_state.get(SK.FLY_TO)
route_points = de.get_route_points()
route_coords = st.session_state.get("compass.route_coords")
waypoint_ids = st.session_state.get("compass.waypoint_ids", [])
completed_ids = st.session_state[SK.COMPLETED]
offset_x     = float(st.session_state.get(SK.OFFSET_X, OFFSET_X_DEFAULT))
offset_y     = float(st.session_state.get(SK.OFFSET_Y, OFFSET_Y_DEFAULT))

# ── Build Folium map (Layer-2/3 caching inside build_map) ──────────────────
folium_map = mc.build_map(
    df=df_filtered,
    completed_ids=completed_ids,
    route_points=route_points,
    fly_to=fly_to,
    map_center=map_center,
    map_zoom=map_zoom,
    route_coords=route_coords,
    waypoint_ids=waypoint_ids,
    offset_x=offset_x,
    offset_y=offset_y,
)

# Clear fly_to after it's been consumed by this render
if fly_to is not None:
    st.session_state[SK.FLY_TO] = None

# ── Render map ─────────────────────────────────────────────────────────────
# map_key increments on fly-to or route plan — forces st_folium to re-mount
# with the new center rather than returning stale position.
map_output = st_folium(
    folium_map,
    width="100%",
    height=720,
    returned_objects=["last_object_clicked"],
    key=f"folium_map_{st.session_state['compass.map_key']}",
)

# ── Persist zoom/center from user panning ─────────────────────────────────
# st_folium returns current center & zoom after user interaction.
# We persist them so the next rerun restores the user's view position.
if map_output and map_output.get("center"):
    c = map_output["center"]
    if isinstance(c, dict):
        st.session_state[SK.MAP_CENTER] = (c.get("lat", map_center[0]),
                                            c.get("lng", map_center[1]))
    elif isinstance(c, (list, tuple)) and len(c) == 2:
        st.session_state[SK.MAP_CENTER] = (float(c[0]), float(c[1]))

if map_output and map_output.get("zoom"):
    st.session_state[SK.MAP_ZOOM] = map_output["zoom"]

# ── Map click info strip ───────────────────────────────────────────────────
clicked = map_output.get("last_object_clicked") if map_output else None
if clicked:
    clat = clicked.get("lat") or clicked.get("x")
    clng = clicked.get("lng") or clicked.get("y")
    if clat is not None and clng is not None:
        st.markdown(
            f'<div style="font-size:0.75rem;color:#7a6e5a;margin-top:0.3rem;">'
            f"📍 Last clicked: lat={clat:.4f}, lng={clng:.4f}</div>",
            unsafe_allow_html=True,
        )
