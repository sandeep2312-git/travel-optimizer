import pandas as pd
import streamlit as st
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from src.planner import plan_itinerary
from src.poi_sources_overpass import fetch_pois
from src.export_pdf import itinerary_to_pdf
from src.export_ics import itinerary_to_ics


# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(page_title="AI Travel Optimizer", layout="wide")
st.title("🧭 AI Travel Optimizer")
st.caption("A layman-friendly itinerary builder that balances **time, budget, distance, and your interests**.")


# ----------------------------
# Helpers
# ----------------------------
@st.cache_data(show_spinner=False)
def geocode_city(city: str):
    """
    Robust geocoding for Codespaces:
    - Uses Nominatim via geopy
    - Tries multiple query variants
    - Retries a few times to handle transient failures/rate-limits
    """
    geolocator = Nominatim(user_agent="travel-optimizer-app (github-codespace)")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)

    queries = [
        city.strip(),
        f"{city.strip()}, USA",
        f"{city.strip()}, United States",
    ]

    for q in queries:
        for _ in range(3):  # retries
            try:
                loc = geocode(q)
                if loc:
                    return float(loc.latitude), float(loc.longitude)
            except Exception:
                pass
    return None


@st.cache_data(show_spinner=False)
def load_pois_cached(lat: float, lon: float, radius_km: float, limit: int):
    return fetch_pois(lat=lat, lon=lon, radius_km=radius_km, limit=limit)


def normalize_poi_defaults(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in [
        ("avg_cost", 15),
        ("visit_duration_mins", 90),
        ("rating", 4.3),
        ("category", "other"),
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


def explain_plan(itinerary: dict, prefs: dict, pace: str, travel_mode: str) -> str:
    if not itinerary.get("days"):
        return "I couldn’t build an itinerary with the current constraints. Try selecting more places, increasing budget, or choosing a relaxed pace."

    top_pref = max(prefs.items(), key=lambda x: x[1])[0]
    total_days = len(itinerary["days"])
    total_cost = itinerary.get("total_cost", 0)
    remaining = itinerary.get("remaining_budget", 0)

    return (
        f"This plan uses **{travel_mode}** travel mode and a **{pace}** pace. "
        f"It prioritizes **{top_pref}** based on your sliders and tries to keep stops **closer together** to reduce travel time. "
        f"Estimated spend is **${total_cost}**, leaving **${remaining}** buffer for meals, tickets, and extras."
    )


# ----------------------------
# Sidebar: Inputs
# ----------------------------
with st.sidebar:
    st.header("1) Trip Inputs")
    city = st.text_input("City", value="Denver, CO")
    trip_start_date = st.date_input("Trip start date").isoformat()

    days = st.slider("Number of days", 1, 7, 3)
    budget = st.number_input("Total budget ($)", min_value=0, value=400, step=50)

    start_hour = st.slider("Day starts at", 6, 12, 10)
    pace = st.selectbox("Pace", ["relaxed", "moderate", "packed"], index=1)
    travel_mode = st.selectbox("Travel mode", ["drive", "transit", "walk"], index=0)

    st.divider()
    st.header("2) Place Search")
    radius_km = st.slider("Search radius (km)", 2, 30, 8)
    max_pois = st.slider("Max places to load", 30, 250, 120, step=10)
    force_refresh = st.checkbox("Force refresh places (ignore cache)", value=False)

    st.divider()
    st.header("3) Interests (weights)")
    nature = st.slider("Nature", 0.0, 1.0, 0.6)
    food = st.slider("Food", 0.0, 1.0, 0.6)
    museums = st.slider("Museums", 0.0, 1.0, 0.4)
    nightlife = st.slider("Nightlife", 0.0, 1.0, 0.2)

prefs = {"nature": nature, "food": food, "museums": museums, "nightlife": nightlife}


# ----------------------------
# Geocode (with manual fallback)
# ----------------------------
coords = None
if city.strip():
    with st.spinner("Finding your city on the map..."):
        coords = geocode_city(city.strip())

if not coords:
    st.warning("City lookup failed (geocoding). You can still continue by entering coordinates manually.")
    c1, c2 = st.columns(2)
    with c1:
        lat = st.number_input("Latitude", value=39.7392, format="%.6f")
    with c2:
        lon = st.number_input("Longitude", value=-104.9903, format="%.6f")
    st.info("Tip: In Google Maps, right-click → 'What's here?' to copy coordinates.")
else:
    lat, lon = coords
    st.success(f"Location found: **{city}**  (lat: {lat:.4f}, lon: {lon:.4f})")


# ----------------------------
# Load POIs
# ----------------------------
with st.spinner("Loading places nearby (OpenStreetMap)…"):
    try:
        if force_refresh:
            pois = fetch_pois(lat=lat, lon=lon, radius_km=float(radius_km), limit=int(max_pois))
        else:
            pois = load_pois_cached(lat=lat, lon=lon, radius_km=float(radius_km), limit=int(max_pois))
    except Exception as e:
        st.error(f"Failed to load places. Error: {e}")
        st.stop()

if not pois:
    st.warning("No places found. Try increasing radius or changing the city.")
    st.stop()

df_pois = normalize_poi_defaults(pd.DataFrame(pois))
df_pois["include"] = True


# ----------------------------
# Curate POIs
# ----------------------------
st.subheader("✅ Pick the places the optimizer can use")
st.caption("Uncheck places you don’t like. You can also tweak time/cost so the schedule matches reality.")

c1, c2, c3 = st.columns(3)
with c1:
    category_filter = st.multiselect(
        "Filter by category",
        options=sorted(df_pois["category"].unique().tolist()),
        default=sorted(df_pois["category"].unique().tolist()),
    )
with c2:
    max_cost_filter = st.slider("Max cost per place ($)", 0, 200, 60)
with c3:
    min_rating_filter = st.slider("Min rating (heuristic)", 0.0, 5.0, 4.0, 0.1)

df_view = df_pois[
    (df_pois["category"].isin(category_filter))
    & (df_pois["avg_cost"] <= max_cost_filter)
    & (df_pois["rating"] >= min_rating_filter)
].copy()

edited = st.data_editor(
    df_view[["include", "name", "category", "avg_cost", "visit_duration_mins", "rating"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "include": st.column_config.CheckboxColumn("Use"),
        "avg_cost": st.column_config.NumberColumn("Avg Cost ($)", min_value=0, max_value=500, step=1),
        "visit_duration_mins": st.column_config.NumberColumn("Time (mins)", min_value=15, max_value=480, step=15),
        "rating": st.column_config.NumberColumn("Rating", min_value=0.0, max_value=5.0, step=0.1),
    },
    key="poi_editor",
)

df_view.loc[:, "include"] = edited["include"].values
df_view.loc[:, "avg_cost"] = edited["avg_cost"].values
df_view.loc[:, "visit_duration_mins"] = edited["visit_duration_mins"].values
df_view.loc[:, "rating"] = edited["rating"].values

chosen_df = df_view[df_view["include"]].copy()

# Preserve lat/lon
chosen_df = chosen_df.merge(df_pois[["name", "lat", "lon"]], on="name", how="left", suffixes=("", "_orig"))
chosen_pois = chosen_df[["name", "category", "avg_cost", "visit_duration_mins", "rating", "lat", "lon"]].to_dict("records")

st.write(f"Places selected: **{len(chosen_pois)}**")


# Map preview
st.subheader("🗺 Map preview of selected places")
map_df = pd.DataFrame([{"lat": p["lat"], "lon": p["lon"]} for p in chosen_pois])
if not map_df.empty:
    st.map(map_df)
else:
    st.info("No mappable points selected.")


# ----------------------------
# Generate
# ----------------------------
st.subheader("📅 Generate itinerary")
generate = st.button("Build my itinerary", type="primary")

if generate:
    if len(chosen_pois) < 5:
        st.warning("Select at least ~5 places so the planner has enough options.")
        st.stop()

    with st.spinner("Optimizing your itinerary (including travel time)..."):
        itinerary = plan_itinerary(
            pois=chosen_pois,
            days=int(days),
            budget=float(budget),
            prefs=prefs,
            pace=pace,
            start_hour=int(start_hour),
            travel_mode=travel_mode,
        )

    # Summary metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Estimated Total Cost", f"${itinerary.get('total_cost', 0)}")
    m2.metric("Remaining Budget", f"${itinerary.get('remaining_budget', 0)}")
    m3.metric("Total Time (activities + travel)", f"{itinerary.get('total_time_mins', 0)} mins")

    st.info(explain_plan(itinerary, prefs, pace, travel_mode))

    # Itinerary per day
    for day in itinerary.get("days", []):
        st.markdown("---")
        st.subheader(f"Day {day['day']}  •  Cost: ${day['day_cost']}  •  Time: {day['day_time_mins']} mins")

        timeline = day.get("timeline", [])
        if not timeline:
            st.warning("No activities selected for this day. Try relaxing constraints or selecting more places.")
            continue

        rows = []
        for i, e in enumerate(timeline, start=1):
            rows.append({
                "#": i,
                "Time": f"{fmt_time(int(e['start_min']))} → {fmt_time(int(e['end_min']))}",
                "Place": e["name"],
                "Category": e["category"],
                "Travel from prev (mins)": int(e.get("travel_from_prev_mins", 0)),
                "Travel from prev (km)": round(float(e.get("travel_from_prev_km", 0.0)), 2),
                "Est. Cost ($)": round(float(e.get("avg_cost", 0.0)), 2),
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.caption("Map links:")
        for i, e in enumerate(timeline, start=1):
            lat_e, lon_e = e.get("lat"), e.get("lon")
            if lat_e is None or lon_e is None:
                continue
            st.link_button(
                f"Open {i}. {e['name']} in Google Maps",
                f"https://www.google.com/maps/search/?api=1&query={lat_e},{lon_e}"
            )

    # ----------------------------
    # Export
    # ----------------------------
    st.markdown("---")
    st.subheader("⬇️ Export")

    safe_city = city.replace(" ", "_").replace(",", "")
    pdf_bytes = itinerary_to_pdf(itinerary, title=f"Itinerary for {city}")
    st.download_button(
        "Download PDF",
        data=pdf_bytes,
        file_name=f"itinerary_{safe_city}.pdf",
        mime="application/pdf",
    )

    ics_bytes = itinerary_to_ics(itinerary, trip_start_date=trip_start_date)
    st.download_button(
        "Download Calendar (.ics)",
        data=ics_bytes,
        file_name=f"itinerary_{safe_city}.ics",
        mime="text/calendar",
    )

    with st.expander("🔧 Debug: Raw itinerary JSON"):
        st.json(itinerary)
