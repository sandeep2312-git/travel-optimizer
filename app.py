import pandas as pd
import streamlit as st
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from src.planner import plan_itinerary
from src.poi_sources_overpass import fetch_pois
from src.export_pdf import itinerary_to_pdf
from src.export_ics import itinerary_to_ics


CITY_PRESETS = {
    "— Select a preset (recommended) —": None,
    "Denver, CO": (39.7392, -104.9903),
    "Dallas, TX": (32.7767, -96.7970),
    "Austin, TX": (30.2672, -97.7431),
    "New York, NY": (40.7128, -74.0060),
    "San Francisco, CA": (37.7749, -122.4194),
    "Los Angeles, CA": (34.0522, -118.2437),
    "Chicago, IL": (41.8781, -87.6298),
    "Seattle, WA": (47.6062, -122.3321),
}

ALL_CATEGORIES = ["food", "nature", "museums", "nightlife", "coffee", "shopping", "viewpoints", "events"]

MODE_CONFIG = {
    "Full day": {"start_hour": None, "pace": None, "force_categories": None},
    "Evening outing only": {
        "start_hour": 17,
        "pace": "relaxed",
        "force_categories": ["nature", "museums", "events", "nightlife", "viewpoints"],
    },
    "Night dinner only": {"start_hour": 19, "pace": "relaxed", "force_categories": ["food", "nightlife", "coffee"]},
}


# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(page_title="AI Travel Optimizer", layout="wide")
st.title("🧭 AI Travel Optimizer")
st.caption("Pick what you want (no weights), then generate a simple schedule + map links + exports.")


# ----------------------------
# Helpers
# ----------------------------
@st.cache_data(show_spinner=False)
def geocode_city(city: str):
    geolocator = Nominatim(user_agent="travel-optimizer-app (github-codespace)")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)
    queries = [city.strip(), f"{city.strip()}, USA", f"{city.strip()}, United States"]
    for q in queries:
        for _ in range(3):
            try:
                loc = geocode(q)
                if loc:
                    return float(loc.latitude), float(loc.longitude)
            except Exception:
                pass
    return None


@st.cache_data(show_spinner=False)
def load_pois_cached(lat: float, lon: float, radius_km: float, limit: int, relaxed: bool):
    return fetch_pois(lat=lat, lon=lon, radius_km=radius_km, limit=limit, relaxed=relaxed)


def normalize_poi_defaults(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in [
        ("avg_cost", 15),
        ("visit_duration_mins", 90),
        ("rating", 4.3),
        ("category", "other"),
        ("cuisine", None),
        ("website", None),
        ("phone", None),
        ("opening_hours", None),
        ("description", None),
        ("wikipedia", None),
    ]:
        if col not in df.columns:
            df[col] = default

    df["name"] = df["name"].astype(str)
    df["avg_cost"] = pd.to_numeric(df["avg_cost"], errors="coerce").fillna(15).astype(float)
    df["visit_duration_mins"] = pd.to_numeric(df["visit_duration_mins"], errors="coerce").fillna(90).astype(int)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(4.3).astype(float)
    df["category"] = df["category"].fillna("other").astype(str)

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["name", "lat", "lon"]).copy()
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    return df


def fmt_time(mins: int) -> str:
    h = (mins // 60) % 24
    m = mins % 60
    ampm = "AM" if h < 12 else "PM"
    hh = h if 1 <= h <= 12 else (12 if h == 0 else h - 12)
    return f"{hh}:{m:02d} {ampm}"


def parse_must_visits(text: str) -> list[str]:
    if not text.strip():
        return []
    raw = text.replace("\n", ",")
    items = [x.strip() for x in raw.split(",")]
    return [x for x in items if x]


def prefs_from_categories(selected: list[str]) -> dict:
    base = 0.2
    return {
        "nature": 1.0 if "nature" in selected else base,
        "food": 1.0 if "food" in selected else base,
        "museums": 1.0 if "museums" in selected else base,
        "nightlife": 1.0 if "nightlife" in selected else base,
    }


def reorder_with_must_visits(pois: list[dict], must_visits: list[str]) -> list[dict]:
    if not must_visits:
        return pois

    def hit(name: str) -> int:
        nl = name.lower()
        return 1 if any(mv.lower() in nl for mv in must_visits) else 0

    return sorted(pois, key=lambda p: hit(p.get("name", "")), reverse=True)


# --- NEW: unique widget id ---
def add_uid_column(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["_uid"] = (
        df["name"].astype(str)
        + "|" + df["category"].astype(str)
        + "|" + df["lat"].round(6).astype(str)
        + "|" + df["lon"].round(6).astype(str)
        + "|" + df.index.astype(str)
    )
    return df


def widget_key(prefix: str, row: pd.Series) -> str:
    return f"{prefix}_{row.get('_uid', '')}"


def uid_mask(df: pd.DataFrame, row: pd.Series):
    return df["_uid"] == row["_uid"]


def place_card(row: pd.Series):
    title = row["name"]
    cat = row["category"]
    cuisine = row.get("cuisine")
    website = row.get("website")
    opening_hours = row.get("opening_hours")
    desc = row.get("description")
    wiki = row.get("wikipedia")

    st.markdown(f"**{title}**  ·  *{cat}*")
    meta = []
    if cuisine and cat in ("food", "coffee"):
        meta.append(f"Cuisine: {cuisine}")
    if opening_hours:
        meta.append(f"Hours: {opening_hours}")
    if meta:
        st.caption(" | ".join(meta))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption(f"Est. cost: ${row['avg_cost']}")
    with c2:
        st.caption(f"Time: {int(row['visit_duration_mins'])} mins")
    with c3:
        st.caption(f"Rating: {row.get('rating', 4.3)}")

    b1, b2 = st.columns(2)
    with b1:
        st.link_button("Open in Google Maps", f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}")
    with b2:
        if website:
            st.link_button("Website", website)

    with st.expander("More details"):
        if desc:
            st.write(desc)
        if wiki:
            st.write(f"Wikipedia tag: {wiki}")
        if not desc and not wiki:
            st.caption("No extra details found for this place in OpenStreetMap tags.")


# ----------------------------
# Sidebar
# ----------------------------
with st.sidebar:
    st.header("1) Trip")
    preset = st.selectbox("City preset", list(CITY_PRESETS.keys()), index=1)
    city = st.text_input("City (optional if preset selected)", value="Denver, CO")
    trip_start_date = st.date_input("Trip start date").isoformat()

    mode = st.selectbox("Trip style", list(MODE_CONFIG.keys()), index=0)
    days = st.slider("Days", 1, 7, 3)
    budget = st.number_input("Budget ($)", min_value=0, value=400, step=50)
    travel_mode = st.selectbox("Travel mode", ["drive", "transit", "walk"], index=0)

    st.divider()
    st.header("2) Timing")
    if MODE_CONFIG[mode]["start_hour"] is None:
        start_hour = st.slider("Start time", 6, 12, 10)
    else:
        start_hour = MODE_CONFIG[mode]["start_hour"]
        st.caption(f"Start time fixed: {start_hour}:00")

    if MODE_CONFIG[mode]["pace"] is None:
        pace = st.selectbox("Pace", ["relaxed", "moderate", "packed"], index=1)
    else:
        pace = MODE_CONFIG[mode]["pace"]
        st.caption(f"Pace fixed: {pace}")

    st.divider()
    st.header("3) What do you want?")
    selected_categories = st.multiselect("Choose categories", options=ALL_CATEGORIES, default=[])

    st.divider()
    st.header("4) Must-visit places")
    must_visits = parse_must_visits(
        st.text_area("Type names (comma/new line). Example: Red Rocks, Denver Art Museum", height=90)
    )

    st.divider()
    st.header("5) Place search")
    radius_km = st.slider("Radius (km)", 2, 30, 10)
    max_pois = st.slider("Max places", 30, 250, 120, step=10)
    relaxed_tags = st.checkbox("Relax place matching", value=True)
    force_refresh = st.checkbox("Force refresh", value=False)


# apply forced categories for special modes
forced = MODE_CONFIG[mode]["force_categories"]
if forced:
    if selected_categories:
        selected_categories = [c for c in selected_categories if c in forced]
    if not selected_categories:
        selected_categories = forced


# ----------------------------
# Location
# ----------------------------
coords = None
city_display = city.strip()

if CITY_PRESETS.get(preset):
    coords = CITY_PRESETS[preset]
    city_display = preset
else:
    if city_display:
        with st.spinner("Finding your city on the map..."):
            coords = geocode_city(city_display)

if not coords:
    st.warning("City lookup failed. Enter coordinates manually.")
    c1, c2 = st.columns(2)
    with c1:
        lat = st.number_input("Latitude", value=39.7392, format="%.6f")
    with c2:
        lon = st.number_input("Longitude", value=-104.9903, format="%.6f")
else:
    lat, lon = coords
    st.success(f"Location set: {city_display} (lat: {lat:.4f}, lon: {lon:.4f})")


# ----------------------------
# Load POIs
# ----------------------------
with st.spinner("Loading places (OpenStreetMap)…"):
    try:
        if force_refresh:
            pois = fetch_pois(float(lat), float(lon), float(radius_km), int(max_pois), bool(relaxed_tags))
        else:
            pois = load_pois_cached(float(lat), float(lon), float(radius_km), int(max_pois), bool(relaxed_tags))
    except Exception as e:
        st.error(f"Failed to load places. Error: {e}")
        st.stop()

if not pois:
    st.warning("No places found. Try increasing radius or enabling Relax matching.")
    st.stop()

df = normalize_poi_defaults(pd.DataFrame(pois))
df["category"] = df["category"].where(df["category"].isin(ALL_CATEGORIES), "other")
df["include"] = False  # nothing auto-selected

# add stable unique widget IDs
df = add_uid_column(df)


# ----------------------------
# Browse UI (Tabs + pagination)
# ----------------------------
st.subheader("🧩 Pick places by category")
st.caption("Choose a category, search, then select places. Use Select/Clear to manage fast.")

search = st.text_input("Search (name / cuisine / description / hours)", value="").strip().lower()

df_show = df.copy()
df_show["category"] = df_show["category"].where(df_show["category"].isin(ALL_CATEGORIES), "other")

# If user selected categories, restrict the browse list to those (otherwise show all)
if selected_categories:
    df_show = df_show[df_show["category"].isin(selected_categories)].copy()

# IMPORTANT: remove duplicates so keys don't repeat
df_show = df_show.drop_duplicates(subset=["name", "category", "lat", "lon"]).reset_index(drop=True)

counts = df_show["category"].value_counts().to_dict()


def cat_label(c: str) -> str:
    return f"{c.capitalize()} ({counts.get(c, 0)})"


cat_tabs = ["all"] + ALL_CATEGORIES + ["other"]
tab_titles = [("All" if c == "all" else cat_label(c)) for c in cat_tabs]
tabs = st.tabs(tab_titles)


def apply_search(dfx: pd.DataFrame) -> pd.DataFrame:
    if not search:
        return dfx

    def _contains(row) -> bool:
        blob = " ".join(
            [
                str(row.get("name", "")),
                str(row.get("cuisine", "")),
                str(row.get("description", "")),
                str(row.get("opening_hours", "")),
            ]
        ).lower()
        return search in blob

    return dfx[dfx.apply(_contains, axis=1)].copy()


def render_category(dfx: pd.DataFrame, cat_key: str):
    if dfx.empty:
        st.info("No places in this category (try increasing radius or turning on Relax matching).")
        return

    top = st.columns([1, 1, 2])
    with top[0]:
        if st.button("✅ Select all (this category)", key=f"select_all_{cat_key}"):
            for _, r in dfx.iterrows():
                df.loc[uid_mask(df, r), "include"] = True
            st.rerun()

    with top[1]:
        if st.button("🧹 Clear (this category)", key=f"clear_cat_{cat_key}"):
            for _, r in dfx.iterrows():
                df.loc[uid_mask(df, r), "include"] = False
            st.rerun()

    with top[2]:
        st.caption("Tip: Search for keywords like “sushi”, “brewery”, “museum”, “viewpoint”, etc.")

    st.markdown("---")

    page_size = 16
    total = len(dfx)
    max_pages = max(1, (total - 1) // page_size + 1)
    page = st.number_input("Page", min_value=1, max_value=max_pages, value=1, step=1, key=f"page_{cat_key}")
    start = (page - 1) * page_size
    end = min(total, start + page_size)

    st.caption(f"Showing {start + 1}-{end} of {total}")

    left, right = st.columns(2)
    for idx, (_, r) in enumerate(dfx.iloc[start:end].iterrows()):
        col = left if idx % 2 == 0 else right
        with col:
            current_vals = df.loc[uid_mask(df, r), "include"].values
            current = bool(current_vals[0]) if len(current_vals) else False

            use = st.checkbox(f"Use: {r['name']}", value=current, key=widget_key("use", r))
            df.loc[uid_mask(df, r), "include"] = bool(use)

            place_card(r)
            st.divider()


for tab, cat in zip(tabs, cat_tabs):
    with tab:
        if cat == "all":
            dfx = apply_search(df_show)
            render_category(dfx, "all")
        else:
            dfx = df_show[df_show["category"] == cat].copy()
            dfx = apply_search(dfx)
            render_category(dfx, cat)


# Selected summary + map
chosen_df = df[df["include"]].dropna(subset=["lat", "lon"]).copy()
chosen_pois = chosen_df.to_dict("records")

st.markdown("---")
st.subheader("🧺 Selected places")
st.write(f"Selected: **{len(chosen_pois)}**")

if len(chosen_pois) > 0:
    st.map(pd.DataFrame([{"lat": p["lat"], "lon": p["lon"]} for p in chosen_pois]))

    with st.expander("Manage selected places"):
        for _, r in chosen_df.iterrows():
            if st.button(f"Remove: {r['name']}", key=widget_key("rm", r)):
                df.loc[uid_mask(df, r), "include"] = False
                st.rerun()
else:
    st.info("Select a few places to build an itinerary.")


# ----------------------------
# Generate
# ----------------------------
st.markdown("---")
st.subheader("📅 Generate itinerary")
generate = st.button("Build my itinerary", type="primary")

if generate:
    if len(chosen_pois) < 2 and mode in ("Night dinner only", "Evening outing only"):
        st.warning("Pick at least 2 places for this mode.")
        st.stop()
    if len(chosen_pois) < 5 and mode == "Full day":
        st.warning("Pick at least ~5 places for a good full-day plan.")
        st.stop()

    prefs = prefs_from_categories(selected_categories)
    chosen_pois = reorder_with_must_visits(chosen_pois, must_visits)

    itinerary = plan_itinerary(
        pois=chosen_pois,
        days=int(days),
        budget=float(budget),
        prefs=prefs,
        pace=pace,
        start_hour=int(start_hour),
        travel_mode=travel_mode,
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Estimated Total Cost", f"${itinerary.get('total_cost', 0)}")
    m2.metric("Remaining Budget", f"${itinerary.get('remaining_budget', 0)}")
    m3.metric("Total Time", f"{itinerary.get('total_time_mins', 0)} mins")

    for day in itinerary.get("days", []):
        st.markdown("---")
        st.subheader(f"Day {day['day']} • Cost: ${day['day_cost']} • Time: {day['day_time_mins']} mins")

        timeline = day.get("timeline", [])
        if not timeline:
            st.warning("No activities selected for this day.")
            continue

        rows = []
        for i, e in enumerate(timeline, start=1):
            rows.append(
                {
                    "#": i,
                    "Time": f"{fmt_time(int(e['start_min']))} → {fmt_time(int(e['end_min']))}",
                    "Place": e["name"],
                    "Category": e["category"],
                    "Travel (mins)": int(e.get("travel_from_prev_mins", 0)),
                    "Est. Cost ($)": round(float(e.get("avg_cost", 0.0)), 2),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.caption("Map links:")
        for i, e in enumerate(timeline, start=1):
            if e.get("lat") is None or e.get("lon") is None:
                continue
            st.link_button(
                f"Open {i}. {e['name']} in Google Maps",
                f"https://www.google.com/maps/search/?api=1&query={e['lat']},{e['lon']}",
            )

    st.markdown("---")
    st.subheader("⬇️ Export")
    safe_city = (city_display or "trip").replace(" ", "_").replace(",", "")
    pdf_bytes = itinerary_to_pdf(itinerary, title=f"Itinerary for {city_display or 'Trip'}")
    st.download_button("Download PDF", data=pdf_bytes, file_name=f"itinerary_{safe_city}.pdf", mime="application/pdf")
    ics_bytes = itinerary_to_ics(itinerary, trip_start_date=trip_start_date)
    st.download_button(
        "Download Calendar (.ics)", data=ics_bytes, file_name=f"itinerary_{safe_city}.ics", mime="text/calendar"
    )

    with st.expander("🔧 Debug: Raw itinerary JSON"):
        st.json(itinerary)
