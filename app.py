import json
import streamlit as st
from src.planner import plan_itinerary

st.set_page_config(page_title="AI Travel Optimizer", layout="wide")

@st.cache_data
def load_pois(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

st.title("🧭 AI Travel Optimizer (MVP)")
st.caption("Build a day-by-day itinerary that balances time, budget, and your interests.")

pois = load_pois("data/pois_denver.json")

with st.sidebar:
    st.header("Trip Settings")
    days = st.slider("Number of days", 1, 7, 3)
    budget = st.number_input("Total budget ($)", min_value=0, value=300, step=50)
    pace = st.selectbox("Pace", ["relaxed", "moderate", "packed"], index=1)

    st.divider()
    st.subheader("Interests (weights)")
    nature = st.slider("Nature", 0.0, 1.0, 0.6)
    food = st.slider("Food", 0.0, 1.0, 0.6)
    museums = st.slider("Museums", 0.0, 1.0, 0.4)
    nightlife = st.slider("Nightlife", 0.0, 1.0, 0.2)

prefs = {"nature": nature, "food": food, "museums": museums, "nightlife": nightlife}

itinerary = plan_itinerary(
    pois=pois,
    days=days,
    budget=float(budget),
    prefs=prefs,
    pace=pace
)

col1, col2 = st.columns([2, 1])
with col2:
    st.metric("Estimated Total Cost", f"${itinerary['total_cost']}")
    st.metric("Remaining Budget", f"${itinerary['remaining_budget']}")
    st.metric("Total Activity Time", f"{itinerary['total_time_mins']} mins")
    st.info("Tip: If you get empty days, increase budget or choose a more relaxed pace.")

with col1:
    for day in itinerary["days"]:
        st.subheader(f"Day {day['day']}  •  Cost: ${day['day_cost']}  •  Time: {day['day_time_mins']} mins")
        if not day["items"]:
            st.warning("No activities selected for this day. Try relaxing constraints.")
            continue

        for idx, poi in enumerate(day["items"], start=1):
            name = poi.get("name", "Unknown")
            cat = poi.get("category", "other")
            cost = poi.get("avg_cost", 0)
            dur = poi.get("visit_duration_mins", 60)
            lat, lon = poi.get("lat"), poi.get("lon")

            with st.container(border=True):
                st.markdown(f"**{idx}. {name}**  \nCategory: `{cat}`  \n⏱ {dur} mins  •  💵 ${cost}")
                if lat is not None and lon is not None:
                    st.link_button("Open in Google Maps",
                                   f"https://www.google.com/maps/search/?api=1&query={lat},{lon}")

st.divider()
st.subheader("Raw Itinerary JSON (for debugging/export)")
st.json(itinerary)
