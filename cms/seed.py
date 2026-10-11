"""Demo data for previews and testing. Run: flask seed-demo

Everything created here is sample content. Delete the database file (or run
`flask reset-demo`, see README) before going live.
"""
import io
import json
import os
import shutil
import secrets
from datetime import timedelta

from flask import current_app
from PIL import Image, ImageDraw, ImageFont

from .db import execute, init_db, query, store_media
from .utils import make_ref, make_token, today_sast

GALLERY_DIR = "img/gallery"


def _copy_gallery(name, prefix):
    """Copy a bundled crew photo into uploads so demo updates behave like real uploads."""
    src = os.path.join(current_app.static_folder, GALLERY_DIR, f"{name}.jpg")
    dest_name = f"{prefix}-{name}-{secrets.token_hex(3)}.jpg"
    with open(src, "rb") as fh:
        store_media(dest_name, "image/jpeg", fh.read())
    return dest_name


def _placeholder_logo(label, colour):
    img = Image.new("RGBA", (480, 200), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 476, 196), radius=28, outline=colour, width=6)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
        small = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = small = ImageFont.load_default()
    d.text((240, 88), label, fill=colour, font=font, anchor="mm")
    d.text((240, 140), "sample sponsor", fill=colour, font=small, anchor="mm")
    name = f"sponsor-demo-{secrets.token_hex(3)}.png"
    buf = io.BytesIO()
    img.save(buf, "PNG")
    store_media(name, "image/png", buf.getvalue())
    return name


def seed_demo():
    init_db()
    if query("SELECT 1 FROM projects LIMIT 1", one=True):
        print("Projects already exist — skipping demo seed.")
        return
    today = today_sast()

    def add_request(street, suburb, city, postcode, desc, name, email, status="submitted", pref="once", photo=None):
        return execute(
            """INSERT INTO requests(ref, token, street_name, suburb, city, postcode, description, service_pref,
                   funding_days, photo, requester_name, email, phone, consent, status)
               VALUES (?,?,?,?,?,?,?,?,21,?,?,?,'',1,?)""",
            (make_ref(), make_token(), street, suburb, city, postcode, desc, pref, photo, name, email, status))

    def add_project(rid, slug, title, street, suburb, city, summary, scope, items, goal, min_c, days, status,
                    cover, service="once", budget=None):
        pid = execute(
            """INSERT INTO projects(slug, request_id, title, street_name, suburb, city, summary, scope, service_type,
                   line_items, budget_cents, goal_cents, min_contribution_cents, deadline, status, cover_image,
                   funded_at, completed_at, scheduled_for)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (slug, rid, title, street, suburb, city, summary, scope, service, json.dumps(items),
             budget or goal, goal, min_c, (today + timedelta(days=days)).isoformat(), status, cover,
             "2026-09-20 10:00:00" if status in ("funded", "scheduled", "in_progress", "completed") else None,
             "2026-09-28 15:00:00" if status == "completed" else None,
             (today + timedelta(days=4)).isoformat() if status in ("scheduled",) else None))
        execute("UPDATE requests SET status='approved', project_id=? WHERE id=?", (pid, rid))
        execute("INSERT INTO quotes(request_id, once_cents, monthly_cents, line_items, scope) VALUES (?,?,?,?,?)",
                (rid, goal if service == "once" else 0, goal if service == "monthly" else 0, json.dumps(items), scope))
        return pid

    def back(pid, people):
        for i, (name, cents, msg) in enumerate(people):
            execute(
                """INSERT INTO contributions(project_id, name, email, anonymous, message, amount_cents, status,
                       checkout_id, payment_id, return_token, paid_at)
                   VALUES (?,?,?,?,?,?, 'paid', ?, ?, ?, datetime('now', ?))""",
                (pid, name, f"demo{pid}{i}@example.com", 1 if name == "Anonymous" else 0, msg, cents,
                 f"demo_{secrets.token_hex(6)}", f"demo_pay_{i}", make_token(), f"-{i * 7 + 3} hours"))

    def update(pid, kind, phase, title, body="", image=None, yt=None, ago=0):
        execute("INSERT INTO updates(project_id, kind, phase, title, body, image, youtube_id, created_at) "
                "VALUES (?,?,?,?,?,?,?, datetime('now', ?))", (pid, kind, phase, title, body, image, yt, f"-{ago} hours"))

    std_items = [
        {"label": "Verge brush-cutting and weed removal (both sides)", "cents": 520000},
        {"label": "Storm-water drain clearing (6 drains)", "cents": 480000},
        {"label": "Pavement sweeping and power-brushing", "cents": 350000},
        {"label": "Debris removal and legal dumping fees", "cents": 300000},
        {"label": "Traffic safety: cones, signage, marshal", "cents": 200000},
    ]

    # 1. Raising funds
    r1 = add_request("Beyers Naudé Drive", "Northcliff", "Johannesburg", "2195",
                     "Service lane verges are overgrown, two drains are blocked with sand and the pavement is covered.",
                     "Demo Requester", "demo1@example.com", pref="once", photo=_copy_gallery("verge-dig", "request"))
    p1 = add_project(r1, "beyers-naude-drive-northcliff", "Beyers Naudé Drive service lane", "Beyers Naudé Drive",
                     "Northcliff", "Johannesburg",
                     "A full one-off clean of the service lane between the two main intersections: verges, drains and pavements.",
                     "Both verges cut back to kerb, 6 storm-water drains opened and cleared, pavements swept and "
                     "power-brushed, all debris removed to a registered site.",
                     std_items, 1850000, 10000, 12, "funding", _copy_gallery("crew-digging", "cover"))
    back(p1, [("Thandi M.", 50000, "Happy to help — this lane floods every summer."),
              ("Pieter V.", 100000, ""), ("Anonymous", 25000, ""), ("Lerato K.", 150000, "For the kids who walk to school here."),
              ("Johan & Annelie", 200000, ""), ("Sipho N.", 30000, ""), ("Anonymous", 50000, "Let's go Northcliff!"),
              ("Ayesha P.", 100000, ""), ("Michael R.", 75000, ""), ("Nomsa D.", 40000, "")])
    update(p1, "photo", "before", "Before: the drain on the corner", "Sand has completely covered the drain grate.",
           image=_copy_gallery("drain-before", "update"), ago=40)
    update(p1, "note", "general", "Quote approved — funding is open",
           "Share the link on your street WhatsApp group. If we don't reach the goal by the deadline, everyone is refunded automatically.",
           ago=60)

    # 2. In progress
    r2 = add_request("Rustenburg Road", "Emmarentia", "Johannesburg", "2195",
                     "Pavement weeds, sand build-up in the gutters and illegal dumping next to the park fence.",
                     "Demo Requester", "demo2@example.com")
    p2 = add_project(r2, "rustenburg-road-emmarentia", "Rustenburg Road park edge", "Rustenburg Road", "Emmarentia",
                     "Johannesburg", "Clearing the park-side pavement, gutters and dumped rubble.",
                     "Pavement weeding, gutter de-silting, rubble removal along the park fence.",
                     std_items[:4], 1200000, 10000, -3, "in_progress", _copy_gallery("crew-pavement", "cover"))
    back(p2, [("Grace T.", 300000, ""), ("Anonymous", 200000, ""), ("Daniel F.", 250000, "Great initiative"),
              ("Emmarentia Neighbours", 450000, "On behalf of 9 households")])
    update(p2, "photo", "during", "Crew on site", "Cones are out and the crew started at 7am.",
           image=_copy_gallery("crew-site", "update"), ago=5)
    update(p2, "photo", "during", "Lifting weeds from the paving", image=_copy_gallery("crew-smile", "update"), ago=4)
    update(p2, "photo", "during", "Heavy rubble removed", image=_copy_gallery("crew-slab", "update"), ago=3)

    # 3. Completed
    r3 = add_request("4th Avenue", "Linden", "Johannesburg", "2195",
                     "Verges and drains on the hill are full of sand after the storms.", "Demo Requester",
                     "demo3@example.com")
    p3 = add_project(r3, "4th-avenue-linden", "4th Avenue hill", "4th Avenue", "Linden", "Johannesburg",
                     "Sand and silt cleared from drains and verges after the summer storms.",
                     "Drain clearing, sand removal, verge cut.", std_items[1:4], 1150000, 5000, -20, "completed",
                     _copy_gallery("aerial-crew", "cover"))
    back(p3, [("Kabelo S.", 200000, ""), ("Anonymous", 150000, ""), ("Riana B.", 300000, ""),
              ("Hassan A.", 250000, "Worth every rand"), ("Zanele M.", 250000, "")])
    update(p3, "photo", "before", "Before: drain slab lifted and blocked", image=_copy_gallery("drain-before", "update"), ago=500)
    update(p3, "photo", "during", "Clearing the drain", image=_copy_gallery("drain-clearing", "update"), ago=470)
    update(p3, "photo", "during", "Power-brushing the paving", image=_copy_gallery("power-brush-closeup", "update"), ago=468)
    update(p3, "photo", "after", "After: swept and clear", image=_copy_gallery("aerial-sweeper", "update"), ago=460)
    update(p3, "note", "after", "All done — thank you, 4th Avenue!",
           "Six drains cleared and 3.2 tonnes of sand and debris removed.", ago=455)

    # 4. Monthly maintenance, raising funds
    r4 = add_request("Jan Smuts Avenue service lane", "Craighall", "Johannesburg", "2196",
                     "We'd like a monthly maintenance clean after the initial one.", "Demo Requester",
                     "demo4@example.com", pref="monthly")
    p4 = add_project(r4, "jan-smuts-service-lane-craighall", "Jan Smuts service lane — monthly upkeep",
                     "Jan Smuts Avenue service lane", "Craighall", "Johannesburg",
                     "A monthly maintenance visit to keep the lane clean after its initial clean.",
                     "Monthly: sweep, weed, clear drains, remove litter.",
                     [{"label": "Monthly maintenance visit (crew of 4, half day)", "cents": 450000}],
                     450000, 5000, 18, "funding", _copy_gallery("brushcutter", "cover"), service="monthly")
    back(p4, [("Craighall Residents", 120000, ""), ("Anonymous", 50000, ""), ("Bongani L.", 30000, "")])

    # Incoming requests for the admin queue
    add_request("Main Road", "Kenilworth", "Cape Town", "7708",
                "Litter and overgrown verges outside the shops; drains block every winter.", "Demo Requester",
                "demo5@example.com", photo=_copy_gallery("verge-debris", "request"))
    add_request("Florida Road", "Morningside", "Durban", "4001",
                "Weeds through the paving and sand in the gutters along the upper section.", "Demo Requester",
                "demo6@example.com", pref="monthly")
    add_request("Lynnwood Road", "Menlo Park", "Pretoria", "0081",
                "Verge needs cutting back; a lot of dumped garden waste.", "Demo Requester", "demo7@example.com",
                status="under_review")

    for label, colour in (("Your Brand", (35, 48, 102)), ("Local Hero", (4, 50, 154)),
                          ("Neighbour Co.", (44, 123, 182)), ("Green Partner", (102, 150, 30))):
        execute("INSERT INTO sponsors(name, logo, url, blurb, sort_order, active) VALUES (?,?,?,?,?,1)",
                (f"{label} (sample)", _placeholder_logo(label, colour), "", "Sample sponsor slot",
                 query("SELECT COUNT(*) n FROM sponsors", one=True)["n"]))
