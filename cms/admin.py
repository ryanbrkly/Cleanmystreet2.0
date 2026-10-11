"""Admin portal: requests, quoting, projects, progress media, sponsors, settings, payments."""
import csv
import io
import json
import time
from functools import wraps

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash

from . import notify
from .db import execute, get_settings, log_activity, now_iso, query, set_setting
from .services import (REQUEST_STATUS_LABELS, STATUS_LABELS, close_and_refund, create_project_from_request,
                       email_backers, get_project, open_next_monthly_round, process_expired_projects,
                       project_url, project_view, refund_contribution)
from .utils import (clean_text, delete_upload, safe_url, line_items_text, load_json, parse_line_items, parse_rand, rand,
                    save_image, youtube_id)

bp = Blueprint("admin", __name__, url_prefix="/admin")

_login_attempts = {}
_DUMMY_HASH = "scrypt:32768:8:1$dummysaltvalue12$" + "0" * 128


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        admin_id = session.get("admin_id")
        admin = query("SELECT * FROM admins WHERE id=?", (admin_id,), one=True) if admin_id else None
        if not admin:
            return redirect(url_for("admin.login", next=request.path))
        g.admin = admin
        g.admin_email = admin["email"]
        return view(*args, **kwargs)
    return wrapped


@bp.context_processor
def admin_ctx():
    counts = {}
    if session.get("admin_id"):
        counts = {
            "new_requests": query("SELECT COUNT(*) n FROM requests WHERE status IN ('submitted','under_review')",
                                  one=True)["n"],
            "refunds_pending": query("SELECT COUNT(*) n FROM contributions WHERE status='refund_pending'",
                                     one=True)["n"],
        }
    return {"nav_counts": counts, "STATUS_LABELS": STATUS_LABELS, "REQUEST_STATUS_LABELS": REQUEST_STATUS_LABELS}


# ---------------------------------------------------------------- auth


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        window = [t for t in _login_attempts.get(ip, []) if t > time.time() - 900]
        if len(window) >= 8:
            flash("Too many attempts. Please wait 15 minutes and try again.", "error")
            return render_template("admin/login.html"), 429
        email = request.form.get("email", "").strip().lower()
        admin = query("SELECT * FROM admins WHERE email=?", (email,), one=True)
        # Always run a hash check so response time doesn't reveal whether the email exists.
        hash_to_check = admin["password_hash"] if admin else _DUMMY_HASH
        password_ok = check_password_hash(hash_to_check, request.form.get("password", ""))
        if admin and password_ok:
            session.clear()
            session["admin_id"] = admin["id"]
            session.permanent = True
            _login_attempts.pop(ip, None)
            log_activity("login", "", admin_email=admin["email"])
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("admin.dashboard"))
        window.append(time.time())
        _login_attempts[ip] = window
        flash("That email and password don’t match.", "error")
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------- dashboard


@bp.route("/")
@login_required
def dashboard():
    expired = process_expired_projects()  # cheap, idempotent safety net in addition to cron
    for title, outcome in expired:
        flash(f"Deadline reached — {title}: {outcome}.", "info")
    kpi = {
        "new_requests": query("SELECT COUNT(*) n FROM requests WHERE status IN ('submitted','under_review')",
                              one=True)["n"],
        "revisions": query("SELECT COUNT(*) n FROM requests WHERE status='revision_requested'", one=True)["n"],
        "funding": query("SELECT COUNT(*) n FROM projects WHERE status='funding'", one=True)["n"],
        "to_schedule": query("SELECT COUNT(*) n FROM projects WHERE status IN ('funded','scheduled','in_progress')",
                             one=True)["n"],
        "raised_30": query("SELECT COALESCE(SUM(amount_cents),0) n FROM contributions WHERE status='paid' "
                           "AND paid_at >= datetime('now','-30 days')", one=True)["n"],
        "raised_all": query("SELECT COALESCE(SUM(amount_cents),0) n FROM contributions WHERE status='paid'",
                            one=True)["n"],
        "refunds_pending": query("SELECT COUNT(*) n FROM contributions WHERE status='refund_pending'",
                                 one=True)["n"],
    }
    queue = query("SELECT * FROM requests WHERE status IN ('submitted','under_review') ORDER BY created_at LIMIT 8")
    live = [project_view(r) for r in query(
        "SELECT * FROM projects WHERE status IN ('funding','funded','scheduled','in_progress') ORDER BY deadline")]
    recent = query("SELECT c.*, p.title FROM contributions c JOIN projects p ON p.id=c.project_id "
                   "WHERE c.status IN ('paid','refunded','refund_pending') ORDER BY COALESCE(c.paid_at,c.created_at) DESC LIMIT 8")
    activity = query("SELECT * FROM activity ORDER BY id DESC LIMIT 10")
    return render_template("admin/dashboard.html", kpi=kpi, queue=queue, live=live, recent=recent,
                           activity=activity)


# ---------------------------------------------------------------- requests & quotes


@bp.route("/requests")
@login_required
def requests_list():
    tab = request.args.get("tab", "open")
    tabs = {
        "open": ("submitted", "under_review"),
        "revision": ("revision_requested",),
        "approved": ("approved",),
        "rejected": ("rejected",),
    }
    statuses = tabs.get(tab, tabs["open"])
    rows = query(f"SELECT r.*, (SELECT COUNT(*) FROM quotes q WHERE q.request_id=r.id) AS has_quote "
                 f"FROM requests r WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at DESC",
                 statuses)
    counts = {k: query(f"SELECT COUNT(*) n FROM requests WHERE status IN ({','.join('?' * len(v))})", v,
                       one=True)["n"] for k, v in tabs.items()}
    return render_template("admin/requests.html", rows=rows, tab=tab, counts=counts)


@bp.route("/requests/<int:rid>", methods=["GET", "POST"])
@login_required
def request_detail(rid):
    req = query("SELECT * FROM requests WHERE id=?", (rid,), one=True)
    if not req:
        abort(404)
    quote = query("SELECT * FROM quotes WHERE request_id=? ORDER BY id DESC", (rid,), one=True)
    settings = get_settings()

    if request.method == "POST":
        action = request.form.get("action")
        track_url = url_for("public.track", token=req["token"], _external=True)

        if action == "save_quote":
            items = parse_line_items(request.form.get("line_items"))
            once = parse_rand(request.form.get("once_amount"))
            monthly = parse_rand(request.form.get("monthly_amount"))
            if not once and items:
                once = sum(i["cents"] for i in items)
            data = (once, monthly, json.dumps(items), request.form.get("scope", "").strip(), now_iso())
            if quote:
                execute("UPDATE quotes SET once_cents=?, monthly_cents=?, line_items=?, scope=?, updated_at=? "
                        "WHERE id=?", data + (quote["id"],))
            else:
                execute("INSERT INTO quotes(once_cents, monthly_cents, line_items, scope, updated_at, request_id) "
                        "VALUES (?,?,?,?,?,?)", data + (rid,))
            if req["status"] == "submitted":
                execute("UPDATE requests SET status='under_review', updated_at=? WHERE id=?", (now_iso(), rid))
            log_activity("quote_saved", f"{req['ref']} once={rand(once)} monthly={rand(monthly)}")
            flash("Quote saved.", "success")

        elif action == "approve":
            if not quote:
                flash("Save a quote before approving.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            service = request.form.get("service_type", "once")
            goal = parse_rand(request.form.get("goal_amount")) or (
                quote["monthly_cents"] if service == "monthly" else quote["once_cents"])
            budget = parse_rand(request.form.get("budget_amount")) or goal
            min_c = parse_rand(request.form.get("min_contribution")) or int(
                settings["default_min_contribution_cents"])
            days = int(request.form.get("funding_days") or req["funding_days"] or 21)
            if goal <= 0:
                flash("The funding goal must be more than R0.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            if min_c > goal:
                flash("The minimum contribution can't be larger than the funding goal.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            title = request.form.get("title", "").strip() or f"{req['street_name']}, {req['suburb'] or req['city']}"
            pid = create_project_from_request(
                req, quote, service_type=service, goal_cents=goal, budget_cents=budget,
                min_contribution_cents=min_c, funding_days=days, title=title,
                summary=request.form.get("summary", "").strip() or req["description"])
            project = get_project(pid)
            notify.send(req["email"], f"Approved: your funding link for {req['street_name']}", "request_approved",
                        req=req, project=project, url=project_url(project))
            log_activity("request_approved", f"{req['ref']} → project {pid} goal {rand(goal)}")
            flash("Approved and published. The funding link has been sent to the requester.", "success")
            return redirect(url_for("admin.project_detail", pid=pid))

        elif action == "reject":
            note = request.form.get("note", "").strip()
            execute("UPDATE requests SET status='rejected', admin_note=?, updated_at=? WHERE id=?",
                    (note, now_iso(), rid))
            notify.send(req["email"], f"Update on your request {req['ref']}", "request_rejected",
                        req=req, note=note, url=track_url)
            log_activity("request_rejected", f"{req['ref']}: {note}")
            flash("Request rejected and the requester notified.", "success")

        elif action == "revise":
            note = request.form.get("note", "").strip()
            if not note:
                flash("Tell the requester what needs to change.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            execute("UPDATE requests SET status='revision_requested', admin_note=?, updated_at=? WHERE id=?",
                    (note, now_iso(), rid))
            notify.send(req["email"], f"Please update your request {req['ref']}", "request_revision",
                        req=req, note=note, url=track_url)
            log_activity("revision_requested", f"{req['ref']}: {note}")
            flash("Revision requested. The requester can edit via their tracking link.", "success")

        elif action == "internal_note":
            execute("UPDATE requests SET internal_note=?, updated_at=? WHERE id=?",
                    (request.form.get("internal_note", ""), now_iso(), rid))
            flash("Internal note saved.", "success")

        elif action == "reopen":
            execute("UPDATE requests SET status='under_review', updated_at=? WHERE id=?", (now_iso(), rid))
            flash("Request reopened.", "success")
        return redirect(url_for("admin.request_detail", rid=rid))

    return render_template("admin/request_detail.html", req=req, quote=quote,
                           items_text=line_items_text(load_json(quote["line_items"])) if quote else "",
                           settings=settings,
                           track_url=url_for("public.track", token=req["token"], _external=True))


# ---------------------------------------------------------------- projects


@bp.route("/projects")
@login_required
def projects_list():
    tab = request.args.get("tab", "live")
    tabs = {
        "live": ("funding", "funded", "scheduled", "in_progress"),
        "completed": ("completed",),
        "closed": ("failed", "cancelled"),
    }
    statuses = tabs.get(tab, tabs["live"])
    rows = [project_view(r) for r in query(
        f"SELECT * FROM projects WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at DESC",
        statuses)]
    return render_template("admin/projects.html", rows=rows, tab=tab)


@bp.route("/projects/<int:pid>", methods=["GET", "POST"])
@login_required
def project_detail(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        f = request.form

        if action == "save_details":
            goal = parse_rand(f.get("goal_amount")) or p["goal_cents"]
            min_c = parse_rand(f.get("min_contribution")) or p["min_contribution_cents"]
            if min_c > goal:
                flash("The minimum contribution can't be larger than the goal.", "error")
                return redirect(url_for("admin.project_detail", pid=pid))
            cover = p["cover_image"]
            if request.files.get("cover") and request.files["cover"].filename:
                try:
                    cover = save_image(request.files["cover"], prefix="cover")
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin.project_detail", pid=pid))
            execute(
                """UPDATE projects SET title=?, summary=?, scope=?, line_items=?, budget_cents=?, goal_cents=?,
                       min_contribution_cents=?, deadline=?, scheduled_for=?, cover_image=?, updated_at=?
                   WHERE id=?""",
                (f.get("title", p["title"]).strip(), f.get("summary", "").strip(), f.get("scope", "").strip(),
                 json.dumps(parse_line_items(f.get("line_items"))),
                 parse_rand(f.get("budget_amount")) or p["budget_cents"], goal, min_c,
                 f.get("deadline") or p["deadline"], f.get("scheduled_for") or None, cover, now_iso(), pid))
            log_activity("project_updated", p["title"])
            flash("Project details saved.", "success")

        elif action == "set_status":
            new = f.get("status")
            if new in ("funded", "scheduled", "in_progress", "completed", "funding"):
                stamp_col = {"completed": "completed_at", "funded": "funded_at"}.get(new)
                execute(f"UPDATE projects SET status=?, updated_at=?{', ' + stamp_col + '=COALESCE(' + stamp_col + ', ?)' if stamp_col else ''} WHERE id=?",
                        (new, now_iso(), now_iso(), pid) if stamp_col else (new, now_iso(), pid))
                if new == "completed":
                    email_backers(pid, f"Done! {p['title']} has been cleaned", "project_completed")
                elif new == "scheduled":
                    email_backers(pid, f"{p['title']}: clean scheduled", "project_scheduled")
                log_activity("status_changed", f"{p['title']} → {new}")
                flash(f"Status changed to “{STATUS_LABELS.get(new, new)}”.", "success")

        elif action == "cancel":
            reason = f.get("reason", "").strip() or "Cancelled by Clean My Street"
            ok, total = close_and_refund(pid, "cancelled", reason)
            flash(f"Project cancelled. {ok} of {total} contributions refunded.", "success")

        elif action == "add_photos":
            files = [fs for fs in request.files.getlist("photos") if fs and fs.filename][:20]
            if not files:
                flash("Choose at least one photo.", "error")
            saved = 0
            for fs in files:
                try:
                    name = save_image(fs, prefix="update")
                except ValueError as exc:
                    flash(f"{fs.filename}: {exc}", "error")
                    continue
                execute("INSERT INTO updates(project_id, kind, phase, title, body, image) VALUES (?,?,?,?,?,?)",
                        (pid, "photo", f.get("phase", "during"), f.get("title", "").strip(),
                         f.get("body", "").strip(), name))
                saved += 1
            if saved:
                log_activity("photos_added", f"{p['title']}: {saved}")
                flash(f"{saved} photo{'s' if saved != 1 else ''} published to the project page.", "success")

        elif action == "add_video":
            vid = youtube_id(f.get("youtube_url"))
            if not vid:
                flash("That doesn't look like a YouTube link. Paste the full URL from YouTube's Share button.",
                      "error")
            else:
                execute("INSERT INTO updates(project_id, kind, phase, title, body, youtube_id) VALUES (?,?,?,?,?,?)",
                        (pid, "video", f.get("phase", "general"), f.get("title", "").strip(),
                         f.get("body", "").strip(), vid))
                flash("Video added.", "success")

        elif action == "add_note":
            if f.get("body", "").strip():
                execute("INSERT INTO updates(project_id, kind, phase, title, body) VALUES (?,?,?,?,?)",
                        (pid, "note", f.get("phase", "general"), f.get("title", "").strip(), f["body"].strip()))
                if f.get("notify"):
                    email_backers(pid, f"Update: {p['title']}", "project_update")
                flash("Update posted.", "success")

        elif action == "delete_update":
            u = query("SELECT * FROM updates WHERE id=? AND project_id=?", (f.get("update_id"), pid), one=True)
            if u:
                delete_upload(u["image"])
                execute("DELETE FROM updates WHERE id=?", (u["id"],))
                flash("Update removed.", "success")

        elif action == "refund":
            c = query("SELECT * FROM contributions WHERE id=? AND project_id=?", (f.get("contribution_id"), pid),
                      one=True)
            if c and refund_contribution(c["id"], reason=f.get("reason", "Refunded by admin")):
                log_activity("refund", f"{p['title']}: {c['name']} {rand(c['amount_cents'])}")
                flash(f"Refund issued to {c['name']}.", "success")
            else:
                flash("Refund could not be issued — see the contribution status.", "error")

        elif action == "next_round":
            if p["service_type"] != "monthly":
                abort(400)
            new_id = open_next_monthly_round(p)
            log_activity("monthly_round_opened", p["title"])
            flash("Next monthly round opened.", "success")
            return redirect(url_for("admin.project_detail", pid=new_id))

        return redirect(url_for("admin.project_detail", pid=pid) + (f"#{f.get('anchor')}" if f.get("anchor") else ""))

    p = get_project(pid)
    updates = query("SELECT * FROM updates WHERE project_id=? ORDER BY created_at DESC, id DESC", (pid,))
    contributions = query("SELECT * FROM contributions WHERE project_id=? ORDER BY created_at DESC", (pid,))
    req = query("SELECT * FROM requests WHERE id=?", (p["request_id"],), one=True) if p["request_id"] else None
    return render_template("admin/project_detail.html", p=p, updates=updates, contributions=contributions,
                           req=req, items_text=line_items_text(p["line_items_list"]),
                           public_url=project_url(p))


# ---------------------------------------------------------------- sponsors


@bp.route("/sponsors", methods=["GET", "POST"])
@login_required
def sponsors():
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        sid = f.get("sponsor_id")
        if action in ("create", "update"):
            logo = None
            if request.files.get("logo") and request.files["logo"].filename:
                try:
                    logo = save_image(request.files["logo"], max_side=600, prefix="sponsor")
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin.sponsors"))
            url = safe_url(f.get("url"))
            if action == "create":
                if not f.get("name", "").strip():
                    flash("Sponsor name is required.", "error")
                    return redirect(url_for("admin.sponsors"))
                order = query("SELECT COALESCE(MAX(sort_order),0)+1 n FROM sponsors", one=True)["n"]
                execute("INSERT INTO sponsors(name, logo, url, blurb, sort_order, active) VALUES (?,?,?,?,?,1)",
                        (f["name"].strip(), logo, url, f.get("blurb", "").strip(), order))
                flash("Sponsor added to the homepage.", "success")
            else:
                s = query("SELECT * FROM sponsors WHERE id=?", (sid,), one=True)
                if s:
                    if logo:
                        delete_upload(s["logo"])
                    execute("UPDATE sponsors SET name=?, url=?, blurb=?, logo=COALESCE(?, logo), active=? WHERE id=?",
                            (f.get("name", s["name"]).strip(), url, f.get("blurb", "").strip(), logo,
                             1 if f.get("active") else 0, sid))
                    flash("Sponsor saved.", "success")
        elif action in ("up", "down"):
            rows = list(query("SELECT id FROM sponsors ORDER BY sort_order, id"))
            ids = [r["id"] for r in rows]
            if int(sid) in ids:
                i = ids.index(int(sid))
                j = i - 1 if action == "up" else i + 1
                if 0 <= j < len(ids):
                    ids[i], ids[j] = ids[j], ids[i]
                for n, x in enumerate(ids):
                    execute("UPDATE sponsors SET sort_order=? WHERE id=?", (n, x))
        elif action == "delete":
            s = query("SELECT * FROM sponsors WHERE id=?", (sid,), one=True)
            if s:
                delete_upload(s["logo"])
                execute("DELETE FROM sponsors WHERE id=?", (sid,))
                flash("Sponsor removed.", "success")
        log_activity(f"sponsor_{action}", str(sid or f.get("name", "")))
        return redirect(url_for("admin.sponsors"))
    rows = query("SELECT * FROM sponsors ORDER BY sort_order, id")
    return render_template("admin/sponsors.html", rows=rows)


# ---------------------------------------------------------------- settings


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        f = request.form
        video = f.get("hero_video_url", "").strip()
        if video and not youtube_id(video):
            flash("The homepage video must be a YouTube link.", "error")
            return redirect(url_for("admin.settings_page"))
        for key in ("hero_video_url", "video_heading", "video_caption", "contact_email", "contact_phone",
                    "whatsapp_number", "announcement", "service_areas", "default_funding_days",
                    "legacy_streets", "legacy_contributors"):
            if key in f:
                set_setting(key, f.get(key, "").strip())
        set_setting("default_min_contribution_cents", str(parse_rand(f.get("default_min_contribution"), 5000)))
        set_setting("legacy_raised_cents", str(parse_rand(f.get("legacy_raised"), 0)))
        log_activity("settings_saved")
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings_page"))
    return render_template("admin/settings.html", s=get_settings(),
                           video_id=youtube_id(get_settings().get("hero_video_url")))


# ---------------------------------------------------------------- payments ledger


@bp.route("/payments")
@login_required
def payments_ledger():
    status = request.args.get("status", "")
    sql = ("SELECT c.*, p.title, p.slug FROM contributions c JOIN projects p ON p.id=c.project_id "
           "WHERE c.status != 'pending'")
    args = []
    if status:
        sql += " AND c.status=?"
        args.append(status)
    sql += " ORDER BY c.created_at DESC LIMIT 500"
    rows = query(sql, args)
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "date", "project", "name", "email", "amount_rand", "status", "checkout_id",
                    "payment_id", "refund_id"])
        for r in rows:
            w.writerow([r["id"], r["paid_at"] or r["created_at"], r["title"], r["name"], r["email"],
                        f"{r['amount_cents'] / 100:.2f}", r["status"], r["checkout_id"], r["payment_id"],
                        r["refund_id"]])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=contributions.csv"})
    totals = query("SELECT status, COUNT(*) n, COALESCE(SUM(amount_cents),0) s FROM contributions "
                   "GROUP BY status")
    return render_template("admin/payments.html", rows=rows, status=status,
                           totals={t["status"]: t for t in totals})
