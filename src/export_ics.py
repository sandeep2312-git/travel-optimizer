from datetime import datetime, timedelta, timezone
import uuid

def _fmt_dt(dt: datetime) -> str:
    # ICS wants UTC like 20251216T170000Z
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def minutes_to_dt(base_date: datetime, mins: int) -> datetime:
    return base_date + timedelta(minutes=int(mins))

def itinerary_to_ics(itinerary: dict, trip_start_date: str, timezone_name: str = "America/Denver") -> bytes:
    """
    trip_start_date: 'YYYY-MM-DD' (local date). We generate events per day using that.
    Note: For simplicity, we store UTC times (works well for imports).
    """
    # treat base date as local midnight, then shift by minutes (we later convert to UTC)
    # (No external tz lib used to keep it simple.)
    base = datetime.fromisoformat(trip_start_date).replace(tzinfo=timezone.utc)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Travel Optimizer//EN",
        "CALSCALE:GREGORIAN",
    ]

    for d in itinerary.get("days", []):
        day_idx = int(d.get("day", 1)) - 1
        tl = d.get("timeline", [])
        for e in tl:
            uid = str(uuid.uuid4())
            start = minutes_to_dt(base + timedelta(days=day_idx), int(e["start_min"]))
            end = minutes_to_dt(base + timedelta(days=day_idx), int(e["end_min"]))

            summary = f"{e.get('name','Activity')}"
            desc = f"Category: {e.get('category','other')}\\nEstimated cost: ${e.get('avg_cost',0)}"
            lat, lon = e.get("lat"), e.get("lon")
            if lat is not None and lon is not None:
                desc += f"\\nMaps: https://www.google.com/maps/search/?api=1&query={lat},{lon}"

            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{_fmt_dt(datetime.now(timezone.utc))}",
                f"DTSTART:{_fmt_dt(start)}",
                f"DTEND:{_fmt_dt(end)}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{desc}",
                "END:VEVENT",
            ]

    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
