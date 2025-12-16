import time
import pandas as pd
import streamlit as st
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from src.planner import plan_itinerary
from src.poi_sources_overpass import fetch_pois


# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(page_title="AI Travel Optimizer", layout="wide")
st.title("🧭 AI Travel Optimizer")
st.caption("Build a day-by-day itinerary that balances **time, budget, and your interests**.")


# ----------------------------
# Helpers
# ----------------------------
@st.cache_data(show_spinner=False)
def geocode_city(city: str) -> tuple[float, float] | None:
    """
    Uses Nominatim (OpenStreetMap geocoder) via geopy.
    Cached to avoid re-geocoding the same city repeatedly.
    """
    geolocator = Nominatim(user_agent="travel-optimizer-app")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)
    loc = geocode(city)
    if not loc:
        return None
    return float(loc.latitude), float(loc.longitude)


def explain_plan(itinerary: dict, prefs: dict, pace: str) -> str:
    # Simple rule-based explanation for laymen
    if not itinerary["days"]:
        return "I couldn’t build an itinerary with the current constraints. Try increasing budget, radius, or choosing a relaxed pace."

    # dominant preference
    top_pref = max(prefs.items(), key=lambda x: x[1])[0]
    total_days = len(itinerary["days"])
    total_cost = itinerary.get("total_cost", 0)
    remaining = itinerary.get("remaining_budget", 0)

    return (
        f"This itinerary is optimized for a **{pace}** pace and prioritizes **{top_pref}** based on your interest sliders. "
        f"It spreads activities across **{total_days} day(s)** while keeping you within your budget. "
        f"Estimated spend is **${total_cost}**, leaving about **${remaining}** as buffer for meals, tickets, or surprises."
    )


def normalize_poi_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure columns exist + clean types so planner doesn't break.
    """
    for col, default in [
        ("avg_cost", 15),
        ("visit_duration_mins", 90),
        ("rating", 4.3),
        ("category", "other"),
    ]:
        if col not in df.columns:
            df[col] = default

    df["avg_cost"] = pd.to_numeric(df["avg_cost"], errors="coerce").fillna(15).astype(float)
    df["visit_duration_mins"] = pd.to_numeric(df["visit_duration_mins"], errors="coerce").fillna(90).astype(int)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(4.3).astype(float)
    df["category"] = df["category"].fillna("other").astype(str)

    # keep only usable rows
    df = df.dropna(subset=["name", "lat", "lon"]).copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"]).copy()
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    return df


# ----------------------------
# Sidebar: Step 1 - Trip inputs
# ----------------------------
with st.sidebar:
    st.header("1) Trip Inputs")

    city = st.text_input("City", value="Denver, CO")
    days = st.slider("Number of days", 1, 7, 3)
    budget = st.number_input("Total budget ($)", min_value=0, value=400, step=50)

    radius_km = st.slider("Search radius (km)", 2, 30, 8)
    max_pois = st.slider("Max places to load", 30, 250, 120, step=10)

    pace = st.selectbox("Pace", ["relaxed", "moderate", "packed"], index=1)

    st.divider()
    st.header("2) Interests (weights)")
    nature = st.slider("Nature", 0.0, 1.0, 0.6)
    food = st.slider("Food", 0.0, 1.0, 0.6)
    museums = st.slider("Museums", 0.0, 1.0, 0.4)
    nightlife = st.slider("Nightlife", 0.0, 1.0, 0.2)

prefs = {"nature": nature, "food": food, "museums": museums, "nightlife": nightlife}


# ----------------------------
# Main: Step 1 - Geocode city
# ----------------------------
coords = None
if city.strip():
    with st.spinner("Finding your city on the map..."):
        coords = geocode_city(city.strip())

if not coords:
    st.error("I couldn't find that city. Try adding state/country (e.g., 'Austin, TX' or 'Paris, France').")
    st.stop()

lat, lon = coords
st.success(f"Location found: **{city}**  (lat: {lat:.4f}, lon: {lon:.4f})")


# ----------------------------
# Main: Step 2 - Load POIs
# ----------------------------
with st.spinner("Loading places near you (OpenStreetMap)…"):
    try:
        pois = fetch_pois(lat=lat, lon=lon, radius_km=float(radius_km), limit=int(max_pois))
    except Exception as e:
        st.error(f"Failed to load places. Error: {e}")
        st.stop()

if not pois:
    st.warning("No places found. Try increasing radius or changing the city.")
    st.stop()

df_pois = normalize_poi_defaults(pd.DataFrame(pois))
df_pois["include"] = True


# ----------------------------
# Main: Step 3 - Let user curate POIs
# ----------------------------
st.subheader("✅ Pick the places you want the optimizer to consider")
st.caption("Tip: Uncheck items you don’t like, or adjust cost/time to match your style.")

# Simple filters
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

# Apply edits back to df_view then build chosen POIs
df_view.loc[:, "include"] = edited["include"].values
df_view.loc[:, "avg_cost"] = edited["avg_cost"].values
df_view.loc[:, "visit_duration_mins"] = edited["visit_duration_mins"].values
df_view.loc[:, "rating"] = edited["rating"].values

chosen_df = df_view[df_view["include"]].copy()
chosen_pois = chosen_df.merge(df_pois[["name", "lat", "lon"]], on="name", how="left").to_dict("records")

st.write(f"Places selected: **{len(chosen_pois)}**")


# Map preview
st.subheader("🗺 Map preview of selected places")
map_df = pd.DataFrame([{"lat": p["lat"], "lon": p["lon"]} for p in chosen_pois if "lat" in p and "lon" in p])
if not map_df.empty:
    st.map(map_df)
else:
    st.info("No mappable points selected.")


# ----------------------------
# Main: Step 4 - Build itinerary
# ----------------------------
st.subheader("📅 Generate itinerary")
generate = st.button("Build my itinerary", type="primary")

if generate:
    if len(chosen_pois) < 5:
        st.warning("Select at least ~5 places so the planner has enough options.")
        st.stop()

    with st.spinner("Optimizing your itinerary..."):
        itinerary = plan_itinerary(
            pois=chosen_pois,
            days=int(days),
            budget=float(budget),
            prefs=prefs,
            pace=pace,
        )

    # Summary metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Estimated Total Cost", f"${itinerary.get('total_cost', 0)}")
    m2.metric("Remaining Budget", f"${itinerary.get('remaining_budget', 0)}")
    m3.metric("Total Activity Time", f"{itinerary.get('total_time_mins', 0)} mins")

    st.info(explain_plan(itinerary, prefs, pace))

    # Itinerary cards
    for day in itinerary["days"]:
        st.markdown("---")
        st.subheader(f"Day {day['day']}  •  Cost: ${day['day_cost']}  •  Time: {day['day_time_mins']} mins")

        if not day["items"]:
            st.warning("No activities selected for this day. Try relaxing constraints or selecting more places.")
            continue

        for i, poi in enumerate(day["items"], start=1):
            name = poi.get("name", "Unknown")
            cat = poi.get("category", "other")
            cost = poi.get("avg_cost", 0)
            dur = poi.get("visit_duration_mins", 60)
            plat, plon = poi.get("lat"), poi.get("lon")

            with st.container(border=True):
                st.markdown(f"**{i}. {name}**")
                st.write(f"Category: `{cat}`")
                st.write(f"⏱ Time: **{dur} mins**   •   💵 Est. cost: **${cost}**")
                if plat is not None and plon is not None:
                    st.link_button(
                        "Open in Google Maps",
                        f"https://www.google.com/maps/search/?api=1&query={plat},{plon}"
                    )

    st.markdown("---")
    with st.expander("🔧 Debug: Raw itinerary JSON"):
        st.json(itinerary)
