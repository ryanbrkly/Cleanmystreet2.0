"""Public website routes."""
import json
import secrets

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   url_for)

from . import notify, payments
from .db import execute, get_settings, now_iso, query
from .services import (REQUEST_STATUS_LABELS, get_project_by_slug, mark_contribution_paid,
                       new_return_token, process_expired_projects, project_view, public_stats)
from .utils import (check_csrf, clean_text, load_json, make_ref, make_token, parse_rand, rand, save_image,
                    valid_email, youtube_id)

bp = Blueprint("public", __name__)

GALLERY = [
    ("aerial-crew", "Crew trimming verges and sweeping a pavement, seen from above"),
    ("crew-pavement", "Crew lifting weeds from a paved sidewalk"),
    ("drain-clearing", "Clearing silt from a blocked storm-water drain"),
    ("brushcutter", "Brush-cutting an overgrown verge"),
    ("crew-slab", "Two crew members carrying a broken concrete slab"),
    ("aerial-sweeper", "Mechanical sweeper and crew working a coned-off street"),
    ("power-brush-closeup", "Power brush lifting sand from paving"),
    ("crew-digging", "Crew digging out compacted debris along a fence line"),
    ("verge-debris", "Clearing dumped polystyrene and grass cuttings"),
    ("power-brush-sweep", "Power-brushing sand off the road edge"),
    ("crew-smile", "Crew member shovelling sand from the kerb"),
    ("crew-site", "Crew on site with safety cones in place"),
]


@bp.before_app_request
def _csrf_guard():
    if request.method == "POST" and request.endpoint not in ("public.yoco_webhook", "public.cron_expire"):
        check_csrf()


# ---------------------------------------------------------------- pages


@bp.route("/")
def home():
    settings = get_settings()
    featured = [project_view(r) for r in query(
        "SELECT * FROM projects WHERE status IN ('funding','funded','scheduled','in_progress') "
        "ORDER BY CASE status WHEN 'funding' THEN 0 ELSE 1 END, deadline LIMIT 3")]
    completed = [project_view(r) for r in query(
        "SELECT * FROM projects WHERE status='completed' ORDER BY completed_at DESC LIMIT 3")]
    sponsors = query("SELECT * FROM sponsors WHERE active=1 AND show_on_home=1 ORDER BY sort_order, id")
    return render_template(
        "pages/home.html",
        stats=public_stats(),
        featured=featured,
        completed=completed,
        sponsors=sponsors,
        video_id=youtube_id(settings.get("hero_video_url")),
        gallery=GALLERY,
    )


@bp.route("/projects")
def projects():
    status = request.args.get("status", "open")
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM projects WHERE 1=1"
    args = []
    groups = {
        "open": ("funding",),
        "active": ("funded", "scheduled", "in_progress"),
        "completed": ("completed",),
        "all": ("funding", "funded", "scheduled", "in_progress", "completed"),
    }
    chosen = groups.get(status, groups["open"])
    sql += f" AND status IN ({','.join('?' * len(chosen))})"
    args.extend(chosen)
    if q:
        sql += " AND (street_name LIKE ? OR suburb LIKE ? OR city LIKE ? OR title LIKE ?)"
        args.extend([f"%{q}%"] * 4)
    sql += " ORDER BY CASE status WHEN 'funding' THEN 0 ELSE 1 END, deadline, created_at DESC"
    items = [project_view(r) for r in query(sql, args)]
    return render_template("pages/projects.html", items=items, status=status, q=q, stats=public_stats())


@bp.route("/projects/<slug>")
def project(slug):
    p = get_project_by_slug(slug)
    if not p:
        abort(404)
    updates = query("SELECT * FROM updates WHERE project_id=? ORDER BY created_at DESC, id DESC", (p["id"],))
    backers = query(
        "SELECT name, anonymous, amount_cents, message, paid_at FROM contributions "
        "WHERE project_id=? AND status='paid' ORDER BY paid_at DESC LIMIT 30", (p["id"],))
    rounds = query("SELECT slug, round_no, status FROM projects WHERE request_id=? AND request_id IS NOT NULL "
                   "ORDER BY round_no", (p["request_id"],)) if p["request_id"] else []
    presets = sorted({max(p["min_contribution_cents"], v) for v in (10000, 25000, 50000, 100000)})
    supporters = query("SELECT s.*, ps.amount_cents FROM project_sponsors ps JOIN sponsors s ON s.id=ps.sponsor_id "
                       "WHERE ps.project_id=? AND s.active=1 ORDER BY s.sort_order, s.id", (p["id"],))
    return render_template("pages/project.html", p=p, updates=updates, backers=backers,
                           presets=presets, rounds=rounds, supporters=supporters)


@bp.route("/how-it-works")
def how_it_works():
    return render_template("pages/how_it_works.html")


@bp.route("/about")
def about():
    return render_template("pages/about.html", stats=public_stats())


@bp.route("/sponsors")
def sponsors():
    items = query("""SELECT s.*, (SELECT COUNT(*) FROM project_sponsors ps JOIN projects p ON p.id=ps.project_id
                                 WHERE ps.sponsor_id=s.id AND p.status NOT IN ('cancelled','failed')) AS project_count
                     FROM sponsors s WHERE s.active=1 ORDER BY s.sort_order, s.id""")
    areas = sorted({(r["area"] or "").strip() for r in items if (r["area"] or "").strip()}, key=str.lower)
    area = request.args.get("area", "").strip()
    shown = [r for r in items if (r["area"] or "").strip().lower() == area.lower()] if area else items
    return render_template("pages/sponsors.html", sponsors=shown, areas=areas, area=area, total=len(items))


@bp.route("/sponsors/<slug>")
def sponsor_page(slug):
    s = query("SELECT * FROM sponsors WHERE slug=? AND active=1", (slug,), one=True)
    if not s:
        abort(404)
    rows = query("""SELECT p.*, ps.amount_cents AS sponsor_cents FROM project_sponsors ps JOIN projects p ON p.id=ps.project_id
                    WHERE ps.sponsor_id=? AND p.status NOT IN ('cancelled','failed')
                    ORDER BY CASE p.status WHEN 'funding' THEN 0 WHEN 'completed' THEN 2 ELSE 1 END, p.created_at DESC""",
                 (s["id"],))
    projects = [project_view(r) for r in rows]
    total = sum(r["sponsor_cents"] or 0 for r in rows)
    others = query("SELECT * FROM sponsors WHERE active=1 AND id<>? AND slug IS NOT NULL ORDER BY sort_order, id", (s["id"],))
    area = (s["area"] or "").strip().lower()
    nearby = [o for o in others if area and (o["area"] or "").strip().lower() == area] or ([] if area else others)
    return render_template("pages/sponsor.html", s=s, projects=projects, total_cents=total, nearby=nearby[:4])


@bp.route("/privacy")
def privacy():
    return render_template("pages/privacy.html")


@bp.route("/terms")
def terms():
    return render_template("pages/terms.html")


# ---------------------------------------------------------------- requests


@bp.route("/request", methods=["GET", "POST"])
def request_clean():
    settings = get_settings()
    form = request.form if request.method == "POST" else {}
    errors = {}
    if request.method == "POST":
        if form.get("website"):  # honeypot field, invisible to people; bots fill it in
            return redirect(url_for("public.home"))
        form = _clean_request_form(form)
        errors = _validate_request_form(form)
        photo = None
        if not errors and request.files.get("photo") and request.files["photo"].filename:
            try:
                photo = save_image(request.files["photo"], prefix="request")
            except ValueError as exc:
                errors["photo"] = str(exc)
        if not errors:
            ref, token = make_ref(), make_token()
            execute(
                """INSERT INTO requests(ref, token, street_name, suburb, city, postcode, description,
                       service_pref, funding_days, photo, requester_name, email, phone, consent)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (ref, token, form["street_name"].strip(), form.get("suburb", "").strip(),
                 form["city"].strip(), form["postcode"].strip(), form["description"].strip(),
                 form.get("service_pref", "once"), int(settings.get("default_funding_days") or 21), photo,
                 form["requester_name"].strip(), form["email"].strip().lower(), form.get("phone", "").strip()),
            )
            track_url = url_for("public.track", token=token, _external=True)
            notify.send(form["email"], f"We've received your request ({ref})", "request_received",
                        name=form["requester_name"], ref=ref, street=form["street_name"], url=track_url)
            notify.notify_admins(f"New cleaning request {ref}: {form['street_name']}", "admin_new_request",
                                 ref=ref, street=form["street_name"], city=form["city"],
                                 url=url_for("admin.requests_list", _external=True))
            return redirect(url_for("public.track", token=token, new=1))
    return render_template("pages/request.html", form=form, errors=errors,
                           default_days=int(settings.get("default_funding_days") or 21))


REQUEST_LIMITS = {"street_name": 120, "suburb": 80, "city": 80, "postcode": 4, "description": 2000,
                  "requester_name": 80, "email": 160, "phone": 30}


def _clean_request_form(form):
    data = {k: clean_text(form.get(k), n, multiline=(k == "description")) for k, n in REQUEST_LIMITS.items()}
    data["service_pref"] = form.get("service_pref") if form.get("service_pref") in ("once", "monthly", "unsure") else "once"
    data["consent"] = form.get("consent", "")
    return data


def _validate_request_form(form):
    errors = {}
    for field, label in (("street_name", "Street name"), ("city", "City or town"), ("postcode", "Postcode"),
                         ("description", "Description"), ("requester_name", "Your name")):
        if not form.get(field, "").strip():
            errors[field] = f"{label} is required."
    if form.get("postcode") and not form["postcode"].strip().isdigit():
        errors["postcode"] = "Postcode should be 4 digits."
    if not valid_email(form.get("email")):
        errors["email"] = "Please enter a valid email address."
    if not form.get("consent"):
        errors["consent"] = "Please agree so we can contact you about this request."
    return errors


@bp.route("/track/<token>", methods=["GET", "POST"])
def track(token):
    req = query("SELECT * FROM requests WHERE token=?", (token,), one=True)
    if not req:
        abort(404)
    errors = {}
    if request.method == "POST":
        if req["status"] != "revision_requested":
            abort(400, "This request can't be edited right now.")
        form = _clean_request_form(request.form)
        errors = _validate_request_form(dict(form, consent="1", requester_name=req["requester_name"],
                                             email=req["email"]))
        photo = req["photo"]
        if not errors and request.files.get("photo") and request.files["photo"].filename:
            try:
                photo = save_image(request.files["photo"], prefix="request")
            except ValueError as exc:
                errors["photo"] = str(exc)
        if not errors:
            execute(
                """UPDATE requests SET street_name=?, suburb=?, city=?, postcode=?, description=?,
                       service_pref=?, photo=?, status='submitted', updated_at=? WHERE id=?""",
                (form["street_name"].strip(), form.get("suburb", "").strip(), form["city"].strip(),
                 form["postcode"].strip(), form["description"].strip(), form.get("service_pref", "once"),
                 photo, now_iso(), req["id"]),
            )
            notify.notify_admins(f"Request {req['ref']} updated by requester", "admin_new_request",
                                 ref=req["ref"], street=form["street_name"], city=form["city"],
                                 url=url_for("admin.request_detail", rid=req["id"], _external=True))
            flash("Thanks — your updated request has been sent back to our team.", "success")
            return redirect(url_for("public.track", token=token))
    project = None
    if req["project_id"]:
        row = query("SELECT * FROM projects WHERE id=?", (req["project_id"],), one=True)
        project = project_view(row) if row else None
    return render_template("pages/track.html", req=req, req_dict=dict(req), project=project, errors=errors,
                           status_label=REQUEST_STATUS_LABELS.get(req["status"], req["status"]),
                           is_new=request.args.get("new"))


# ---------------------------------------------------------------- contributions & payments


@bp.route("/projects/<slug>/contribute", methods=["POST"])
def contribute(slug):
    p = get_project_by_slug(slug)
    if not p:
        abort(404)
    if not p["is_open_for_funding"]:
        flash("This project is no longer accepting contributions.", "error")
        return redirect(url_for("public.project", slug=slug))
    form = request.form
    if form.get("website"):  # honeypot
        return redirect(url_for("public.project", slug=slug))
    amount = parse_rand(form.get("amount_custom")) or parse_rand(form.get("amount"))
    name = clean_text(form.get("name"), 80)
    email = clean_text(form.get("email"), 160).lower()
    problems = []
    if amount < p["min_contribution_cents"]:
        problems.append(f"The minimum contribution for this street is {rand(p['min_contribution_cents'])}.")
    if amount > 50_000_000:
        problems.append("For contributions above R500 000 please contact us directly.")
    if not name:
        problems.append("Please enter your name.")
    if not valid_email(email):
        problems.append("Please enter a valid email so we can send your receipt (and any refund).")
    if not form.get("agree"):
        problems.append("Please accept the refund policy and terms.")
    if problems:
        for msg in problems:
            flash(msg, "error")
        return redirect(url_for("public.project", slug=slug) + "#contribute")

    token = new_return_token()
    cid = execute(
        "INSERT INTO contributions(project_id, name, email, anonymous, message, amount_cents, return_token) "
        "VALUES (?,?,?,?,?,?,?)",
        (p["id"], name, email, 1 if form.get("anonymous") else 0, clean_text(form.get("message"), 280),
         amount, token),
    )
    contribution = query("SELECT * FROM contributions WHERE id=?", (cid,), one=True)
    base = current_app.config["PUBLIC_BASE_URL"] or request.host_url.rstrip("/")
    try:
        checkout_id, redirect_url = payments.create_checkout(contribution, p, base)
    except payments.PaymentError:
        execute("UPDATE contributions SET status='failed' WHERE id=?", (cid,))
        flash("We couldn't start the payment just now. Please try again in a minute.", "error")
        return redirect(url_for("public.project", slug=slug) + "#contribute")
    execute("UPDATE contributions SET checkout_id=? WHERE id=?", (checkout_id, cid))
    return redirect(redirect_url, code=303)


def _contribution_by_token(token):
    c = query("SELECT * FROM contributions WHERE return_token=?", (token,), one=True)
    if not c:
        abort(404)
    return c


@bp.route("/pay/demo/<token>", methods=["GET", "POST"])
def pay_demo(token):
    if current_app.config["PAYMENT_MODE"] != "demo":
        abort(404)
    c = _contribution_by_token(token)
    p = project_view(query("SELECT * FROM projects WHERE id=?", (c["project_id"],), one=True))
    if request.method == "POST":
        if request.form.get("outcome") == "success":
            mark_contribution_paid(c["id"], payment_id=f"demo_pay_{secrets.token_hex(4)}")
            return redirect(url_for("public.pay_return", token=token))
        if request.form.get("outcome") == "fail":
            execute("UPDATE contributions SET status='failed' WHERE id=? AND status='pending'", (c["id"],))
            return redirect(url_for("public.pay_return", token=token, failed=1))
        return redirect(url_for("public.pay_cancel", token=token))
    return render_template("pages/pay_demo.html", c=c, p=p)


@bp.route("/pay/return/<token>")
def pay_return(token):
    c = _contribution_by_token(token)
    if c["status"] == "pending" and c["checkout_id"] and current_app.config["PAYMENT_MODE"] == "yoco":
        try:
            checkout = payments.get_checkout(c["checkout_id"])
            if payments.checkout_is_paid(checkout):
                mark_contribution_paid(c["id"], payment_id=checkout.get("paymentId"))
        except payments.PaymentError:
            pass  # the webhook will still confirm it
        c = _contribution_by_token(token)
    if request.args.get("failed") and c["status"] == "pending":
        execute("UPDATE contributions SET status='failed' WHERE id=?", (c["id"],))
        c = _contribution_by_token(token)
    p = project_view(query("SELECT * FROM projects WHERE id=?", (c["project_id"],), one=True))
    return render_template("pages/pay_result.html", c=c, p=p)


@bp.route("/pay/cancel/<token>")
def pay_cancel(token):
    c = _contribution_by_token(token)
    if c["status"] == "pending":
        execute("UPDATE contributions SET status='failed' WHERE id=?", (c["id"],))
    p = query("SELECT slug FROM projects WHERE id=?", (c["project_id"],), one=True)
    flash("Payment cancelled — you haven't been charged.", "info")
    return redirect(url_for("public.project", slug=p["slug"]) + "#contribute")


@bp.route("/webhooks/yoco", methods=["POST"])
def yoco_webhook():
    raw = request.get_data()
    if not payments.verify_webhook(request.headers, raw):
        return Response("invalid signature", status=401)
    msg_id = request.headers.get("webhook-id")
    if query("SELECT 1 FROM webhook_events WHERE id=?", (msg_id,), one=True):
        return Response("duplicate", status=200)
    try:
        event = json.loads(raw)
    except ValueError:
        return Response("bad json", status=400)
    etype = event.get("type", "")
    execute("INSERT INTO webhook_events(id, type, payload) VALUES (?,?,?)", (msg_id, etype, raw.decode()[:20000]))
    payload = event.get("payload") or {}
    meta = payload.get("metadata") or {}
    checkout_id = meta.get("checkoutId") or payload.get("checkoutId")
    c = None
    if checkout_id:
        c = query("SELECT * FROM contributions WHERE checkout_id=?", (checkout_id,), one=True)
    if not c and meta.get("contributionId"):
        c = query("SELECT * FROM contributions WHERE id=?", (meta["contributionId"],), one=True)
    if not c:
        current_app.logger.warning("Webhook %s for unknown checkout %s", etype, checkout_id)
        return Response("ok", status=200)
    if etype == "payment.succeeded":
        mark_contribution_paid(c["id"], payment_id=payload.get("id"))
    elif etype == "payment.failed" and c["status"] == "pending":
        execute("UPDATE contributions SET status='failed' WHERE id=?", (c["id"],))
    elif etype in ("refund.succeeded", "payment.refunded"):
        execute("UPDATE contributions SET status='refunded', refunded_at=? WHERE id=?", (now_iso(), c["id"]))
    elif etype == "refund.failed":
        execute("UPDATE contributions SET status='refund_pending' WHERE id=?", (c["id"],))
    return Response("ok", status=200)


# ---------------------------------------------------------------- ops


@bp.route("/tasks/process-expired", methods=["POST", "GET"])
def cron_expire():
    """Call daily from an external cron: /tasks/process-expired?key=CRON_SECRET"""
    secret = current_app.config["CRON_SECRET"]
    if not secret or not secrets.compare_digest(request.args.get("key", ""), secret):
        abort(404)
    results = process_expired_projects()
    return {"processed": [{"project": t, "outcome": o} for t, o in results]}


@bp.route("/healthz")
def healthz():
    query("SELECT 1")
    turso = bool(current_app.config.get("TURSO_DATABASE_URL"))
    return {"ok": True, "database": "turso" if turso else "local",
            "permanent": not current_app.config.get("TEMP_STORAGE")}


@bp.route("/robots.txt")
def robots():
    base = current_app.config["PUBLIC_BASE_URL"] or request.host_url.rstrip("/")
    body = f"User-agent: *\nDisallow: /admin\nDisallow: /track/\nDisallow: /pay/\nSitemap: {base}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    base = current_app.config["PUBLIC_BASE_URL"] or request.host_url.rstrip("/")
    paths = ["/", "/projects", "/how-it-works", "/about", "/request", "/sponsors", "/privacy", "/terms"]
    paths += [f"/sponsors/{r['slug']}" for r in query("SELECT slug FROM sponsors WHERE active=1 AND slug IS NOT NULL")]
    paths += [f"/projects/{r['slug']}" for r in query(
        "SELECT slug FROM projects WHERE status NOT IN ('cancelled')")]
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in paths)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, mimetype="application/xml")


@bp.app_template_filter("line_items")
def _line_items(text):
    return load_json(text, [])
