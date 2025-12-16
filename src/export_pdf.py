from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

def _fmt_time(mins: int) -> str:
    h = (mins // 60) % 24
    m = mins % 60
    ampm = "AM" if h < 12 else "PM"
    hh = h if 1 <= h <= 12 else (12 if h == 0 else h - 12)
    return f"{hh}:{m:02d} {ampm}"

def itinerary_to_pdf(itinerary: dict, title: str = "AI Travel Optimizer Itinerary") -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    x = 50
    y = height - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, title)

    y -= 30
    c.setFont("Helvetica", 11)
    c.drawString(x, y, f"Estimated total cost: ${itinerary.get('total_cost', 0)}")
    y -= 16
    c.drawString(x, y, f"Remaining budget: ${itinerary.get('remaining_budget', 0)}")
    y -= 20

    for day in itinerary.get("days", []):
        if y < 120:
            c.showPage()
            y = height - 60

        c.setFont("Helvetica-Bold", 13)
        c.drawString(x, y, f"Day {day.get('day')}  |  Cost: ${day.get('day_cost', 0)}  |  Time: {day.get('day_time_mins', 0)} mins")
        y -= 18

        tl = day.get("timeline", [])
        c.setFont("Helvetica", 10)

        if not tl:
            c.drawString(x, y, "No activities planned.")
            y -= 16
            continue

        for i, e in enumerate(tl, start=1):
            if y < 90:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 10)

            line = f"{i}. {_fmt_time(int(e['start_min']))}–{_fmt_time(int(e['end_min']))} | {e.get('name')} | {e.get('category')} | travel {e.get('travel_from_prev_mins',0)}m"
            c.drawString(x, y, line[:110])  # keep line short
            y -= 14

        y -= 10

    c.save()
    return buf.getvalue()
