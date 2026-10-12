"""Admin portal: requests, quoting, projects, progress media, sponsors, settings, payments."""
import csv
import io
import json
import time
from functools import wraps

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash

from . import emails, notify
from .emails import abs_url, project_context, request_context
from .db import execute, get_settings, log_activity, now_iso, query, set_setting
from .services import (REQUEST_STATUS_LABELS, STATUS_LABELS, close_and_refund, create_project_from_request,
                       email_followers, get_project, open_next_monthly_round, process_expired_projects,
                       project_url, project_view, refund_contribution)
from .utils import (clean_text, delete_upload, safe_url, line_items_text, load_json, parse_line_items, parse_rand, rand,
                    save_image, valid_email, youtube_id)
from markupsafe import escape

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
            cover = None
            if request.files.get("cover") and request.files["cover"].filename:
                try:
                    cover = save_image(request.files["cover"], prefix="cover")
                except ValueError as exc:
                    flash(f"Project picture: {exc}", "error")
                    return redirect(url_for("admin.request_detail", rid=rid))
            pid = create_project_from_request(
                req, quote, service_type=service, goal_cents=goal, budget_cents=budget,
                min_contribution_cents=min_c, funding_days=days, title=title,
                summary=request.form.get("summary", "").strip() or req["description"], cover_image=cover)
            project = get_project(pid)
            notify.send(req["email"], "request_approved", "request", rid,
                        **dict(project_context(project), **request_context(req)))
            log_activity("request_approved", f"{req['ref']} → project {pid} goal {rand(goal)}")
            flash("Approved and published. The funding link has been sent to the requester.", "success")
            return redirect(url_for("admin.project_detail", pid=pid))

        elif action == "reject":
            note = request.form.get("note", "").strip()
            execute("UPDATE requests SET status='rejected', admin_note=?, updated_at=? WHERE id=?",
                    (note, now_iso(), rid))
            notify.send(req["email"], "request_rejected", "request", rid, **dict(request_context(req), note=note))
            log_activity("request_rejected", f"{req['ref']}: {note}")
            flash("Request rejected and the requester notified.", "success")

        elif action == "revise":
            note = request.form.get("note", "").strip()
            if not note:
                flash("Tell the requester what needs to change.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            execute("UPDATE requests SET status='revision_requested', admin_note=?, updated_at=? WHERE id=?",
                    (note, now_iso(), rid))
            notify.send(req["email"], "request_revision", "request", rid, **dict(request_context(req), note=note))
            log_activity("revision_requested", f"{req['ref']}: {note}")
            flash("Revision requested. The requester can edit via their tracking link.", "success")

        elif action == "internal_note":
            execute("UPDATE requests SET internal_note=?, updated_at=? WHERE id=?",
                    (request.form.get("internal_note", ""), now_iso(), rid))
            flash("Internal note saved.", "success")

        elif action == "delete_request":
            if req["project_id"]:
                flash("This request has a project. Delete the project first.", "error")
                return redirect(url_for("admin.request_detail", rid=rid))
            if req["photo"]:
                delete_upload(req["photo"])
            execute("DELETE FROM quotes WHERE request_id=?", (rid,))
            execute("DELETE FROM requests WHERE id=?", (rid,))
            log_activity("request_deleted", f"{req['ref']} {req['street_name']}")
            flash(f"Request {req['ref']} deleted.", "success")
            return redirect(url_for("admin.requests_list"))

        elif action == "reopen":
            execute("UPDATE requests SET status='under_review', updated_at=? WHERE id=?", (now_iso(), rid))
            flash("Request reopened.", "success")
        return redirect(url_for("admin.request_detail", rid=rid))

    return render_template("admin/request_detail.html", req=req, quote=quote,
                           items_text=line_items_text(load_json(quote["line_items"])) if quote else "",
                           settings=settings,
                           track_url=url_for("public.track", token=req["token"], _external=True),
                           email_history=emails.history("request", rid))


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


def _cover_in_use(name, project_id):
    """True if a picture file is still used somewhere other than as this project's cover."""
    if not name:
        return False
    return bool(query("SELECT 1 FROM requests WHERE photo=?", (name,), one=True)
                or query("SELECT 1 FROM updates WHERE image=?", (name,), one=True)
                or query("SELECT 1 FROM projects WHERE cover_image=? AND id<>?", (name, project_id), one=True))


def _project_picture(p, action, f):
    old = p["cover_image"]
    if action == "set_cover":
        fs = request.files.get("cover")
        if not fs or not fs.filename:
            flash("Choose a picture to upload.", "error")
            return
        try:
            new = save_image(fs, prefix="cover")
        except ValueError as exc:
            flash(str(exc), "error")
            return
        message = "New project picture saved."
    elif action == "use_photo":
        new = f.get("photo", "")
        allowed = {r["image"] for r in query("SELECT image FROM updates WHERE project_id=? AND image IS NOT NULL",
                                             (p["id"],))}
        if p["request_id"]:
            r = query("SELECT photo FROM requests WHERE id=?", (p["request_id"],), one=True)
            if r and r["photo"]:
                allowed.add(r["photo"])
        if new not in allowed:
            flash("That photo doesn’t belong to this project.", "error")
            return
        message = "Project picture changed."
    elif action == "remove_cover":
        new = None
        message = "Project picture removed. The standard photo is shown instead."
    else:
        pos = f.get("cover_position")
        if pos in ("top", "center", "bottom"):
            execute("UPDATE projects SET cover_position=?, updated_at=? WHERE id=?", (pos, now_iso(), p["id"]))
            flash("Picture position saved.", "success")
        return
    execute("UPDATE projects SET cover_image=?, updated_at=? WHERE id=?", (new, now_iso(), p["id"]))
    if old and old != new and not _cover_in_use(old, p["id"]):
        delete_upload(old)
    log_activity("project_picture", f"{p['title']}: {action}")
    flash(message, "success")


def _notify_update(pid, form, image=None, video=None):
    """Email backers and the requester about a new update, if the admin ticked the box."""
    if not form.get("notify"):
        return
    n = email_followers(pid, "project_update", update_title=form.get("title", "").strip(),
                        update_body=form.get("body", "").strip(),
                        update_image_url=abs_url("static", filename=f"uploads/{image}") if image else "",
                        update_video_url=f"https://youtu.be/{video}" if video else "")
    flash(f"Update emailed to {n} {'person' if n == 1 else 'people'}." if n else
          "Update saved. No emails went out (no backers yet, or email isn’t set up — see Admin → Emails).",
          "success" if n else "info")


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

        elif action in ("set_cover", "use_photo", "remove_cover", "cover_position"):
            _project_picture(p, action, f)
            return redirect(url_for("admin.project_detail", pid=pid) + "#picture")

        elif action == "set_status":
            new = f.get("status")
            if new in ("funded", "scheduled", "in_progress", "completed", "funding"):
                stamp_col = {"completed": "completed_at", "funded": "funded_at"}.get(new)
                execute(f"UPDATE projects SET status=?, updated_at=?{', ' + stamp_col + '=COALESCE(' + stamp_col + ', ?)' if stamp_col else ''} WHERE id=?",
                        (new, now_iso(), now_iso(), pid) if stamp_col else (new, now_iso(), pid))
                key = {"completed": "project_completed", "scheduled": "project_scheduled",
                       "in_progress": "project_in_progress"}.get(new)
                wants = f.get("notify") == "1" or not f.get("has_notify")
                if key and wants and new != p["status"]:
                    n = email_followers(pid, key)
                    if n:
                        flash(f"{n} email{'s' if n != 1 else ''} sent to backers and the requester.", "success")
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
                _notify_update(pid, f, image=name)

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
                _notify_update(pid, f, video=vid)

        elif action == "add_note":
            if f.get("body", "").strip():
                execute("INSERT INTO updates(project_id, kind, phase, title, body) VALUES (?,?,?,?,?)",
                        (pid, "note", f.get("phase", "general"), f.get("title", "").strip(), f["body"].strip()))
                flash("Update posted.", "success")
                _notify_update(pid, f)

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

        elif action == "add_sponsor":
            sponsor_id = f.get("sponsor_id", type=int)
            if sponsor_id and query("SELECT 1 FROM sponsors WHERE id=?", (sponsor_id,), one=True):
                execute("INSERT OR REPLACE INTO project_sponsors(project_id, sponsor_id, amount_cents) VALUES (?,?,?)",
                        (pid, sponsor_id, parse_rand(f.get("amount"))))
                flash("Sponsor added to this project.", "success")

        elif action == "remove_sponsor":
            execute("DELETE FROM project_sponsors WHERE project_id=? AND sponsor_id=?", (pid, f.get("sponsor_id", type=int)))
            flash("Sponsor removed from this project.", "success")

        elif action == "delete_project":
            held = query("SELECT COUNT(*) n FROM contributions WHERE project_id=? AND status IN ('paid','refund_pending')",
                         (pid,), one=True)["n"]
            if held:
                flash(f"This project still holds {held} paid contribution{'s' if held != 1 else ''}. "
                      "Cancel it and refund everyone first, then delete it.", "error")
                return redirect(url_for("admin.project_detail", pid=pid))
            if f.get("confirm_title", "").strip() != p["title"].strip():
                flash("Type the project title exactly to confirm deleting it.", "error")
                return redirect(url_for("admin.project_detail", pid=pid) + "#delete")
            for u in query("SELECT image FROM updates WHERE project_id=? AND image IS NOT NULL", (pid,)):
                delete_upload(u["image"])
            if p["cover_image"] and not _cover_in_use(p["cover_image"], p["id"]):
                delete_upload(p["cover_image"])
            execute("DELETE FROM updates WHERE project_id=?", (pid,))
            execute("DELETE FROM contributions WHERE project_id=?", (pid,))
            execute("DELETE FROM project_sponsors WHERE project_id=?", (pid,))
            execute("UPDATE projects SET parent_id=NULL WHERE parent_id=?", (pid,))
            execute("UPDATE requests SET project_id=NULL, status='rejected', admin_note=?, updated_at=? WHERE project_id=?",
                    ("Project removed by Clean My Street.", now_iso(), pid))
            execute("DELETE FROM projects WHERE id=?", (pid,))
            log_activity("project_deleted", p["title"])
            flash(f"“{p['title']}” was deleted.", "success")
            return redirect(url_for("admin.projects_list"))

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
    project_sponsors = query("SELECT s.*, ps.amount_cents FROM project_sponsors ps JOIN sponsors s ON s.id=ps.sponsor_id "
                             "WHERE ps.project_id=? ORDER BY s.sort_order, s.id", (pid,))
    all_sponsors = query("SELECT id, name FROM sponsors ORDER BY name")
    return render_template("admin/project_detail.html", p=p, updates=updates, contributions=contributions,
                           project_sponsors=project_sponsors, all_sponsors=all_sponsors,
                           req=req, items_text=line_items_text(p["line_items_list"]),
                           public_url=project_url(p), email_history=emails.history("project", pid))


# ---------------------------------------------------------------- sponsors


@bp.route("/sponsors", methods=["GET", "POST"])
@login_required
def sponsors():
    """Sponsor list: reorder, show/hide, delete. Adding and editing happen on the sponsor form."""
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        sid = f.get("sponsor_id", type=int)
        s = query("SELECT * FROM sponsors WHERE id=?", (sid,), one=True) if sid else None
        if action in ("up", "down") and s:
            ids = [r["id"] for r in query("SELECT id FROM sponsors ORDER BY sort_order, id")]
            i = ids.index(sid)
            j = i - 1 if action == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
            for n, x in enumerate(ids):
                execute("UPDATE sponsors SET sort_order=? WHERE id=?", (n, x))
        elif action == "toggle" and s:
            execute("UPDATE sponsors SET active=? WHERE id=?", (0 if s["active"] else 1, sid))
            flash(f"{s['name']} is now {'hidden' if s['active'] else 'shown'} on the website.", "success")
        elif action == "delete" and s:
            delete_upload(s["logo"])
            execute("DELETE FROM project_sponsors WHERE sponsor_id=?", (sid,))
            execute("DELETE FROM sponsors WHERE id=?", (sid,))
            flash(f"{s['name']} removed.", "success")
        log_activity(f"sponsor_{action}", s["name"] if s else "")
        return redirect(url_for("admin.sponsors"))
    rows = query("""SELECT s.*, (SELECT COUNT(*) FROM project_sponsors ps WHERE ps.sponsor_id=s.id) AS project_count
                    FROM sponsors s ORDER BY s.sort_order, s.id""")
    return render_template("admin/sponsors.html", rows=rows)


@bp.route("/sponsors/new", methods=["GET", "POST"])
@bp.route("/sponsors/<int:sid>", methods=["GET", "POST"])
@login_required
def sponsor_edit(sid=None):
    from .db import unique_sponsor_slug
    from .utils import slugify
    s = query("SELECT * FROM sponsors WHERE id=?", (sid,), one=True) if sid else None
    if sid and not s:
        abort(404)
    projects = [project_view(r) for r in query(
        "SELECT * FROM projects WHERE status NOT IN ('cancelled','failed') ORDER BY status='completed', created_at DESC")]
    linked = {r["project_id"]: r for r in query("SELECT * FROM project_sponsors WHERE sponsor_id=?", (sid,))} if sid else {}
    if request.method == "POST":
        f = request.form
        name = clean_text(f.get("name"), 100)
        if not name:
            flash("Give the sponsor a name.", "error")
            return redirect(request.url)
        logo = None
        if request.files.get("logo") and request.files["logo"].filename:
            try:
                logo = save_image(request.files["logo"], max_side=600, prefix="sponsor")
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(request.url)
        data = (name, safe_url(f.get("url")), clean_text(f.get("description"), 1200, multiline=True),
                clean_text(f.get("blurb"), 300), 1 if f.get("active") else 0, 1 if f.get("show_on_home") else 0,
                clean_text(f.get("area"), 80), clean_text(f.get("category"), 60), clean_text(f.get("offer"), 200),
                clean_text(f.get("phone"), 30), clean_text(f.get("address"), 160))
        if s:
            if logo:
                delete_upload(s["logo"])
            execute("UPDATE sponsors SET name=?, url=?, description=?, blurb=?, active=?, show_on_home=?, "
                    "area=?, category=?, offer=?, phone=?, address=?, "
                    "logo=COALESCE(?, logo) WHERE id=?", data + (logo, sid))
        else:
            order = query("SELECT COALESCE(MAX(sort_order),0)+1 n FROM sponsors", one=True)["n"]
            sid = execute("INSERT INTO sponsors(name, url, description, blurb, active, show_on_home, "
                          "area, category, offer, phone, address, logo, sort_order, slug) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", data + (logo, order, unique_sponsor_slug(slugify(name))))
        # project allocations
        chosen = set(f.getlist("projects", type=int))
        for pid_ in chosen:
            amount = parse_rand(f.get(f"amount_{pid_}"))
            if query("SELECT 1 FROM project_sponsors WHERE project_id=? AND sponsor_id=?", (pid_, sid), one=True):
                execute("UPDATE project_sponsors SET amount_cents=? WHERE project_id=? AND sponsor_id=?", (amount, pid_, sid))
            else:
                execute("INSERT INTO project_sponsors(project_id, sponsor_id, amount_cents) VALUES (?,?,?)", (pid_, sid, amount))
        for pid_ in set(linked) - chosen:
            execute("DELETE FROM project_sponsors WHERE project_id=? AND sponsor_id=?", (pid_, sid))
        log_activity("sponsor_saved", f"{name}: {len(chosen)} project(s)")
        flash(f"{name} saved.", "success")
        return redirect(url_for("admin.sponsors"))
    return render_template("admin/sponsor_edit.html", s=s, projects=projects, linked=linked)


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
        if f.get("action", "").startswith("payments_"):
            _payment_settings(f)
            return redirect(url_for("admin.settings_page") + "#payments")
        if f.get("action") == "remove_samples":
            n = remove_sample_data()
            log_activity("sample_data_removed", str(n))
            flash(f"Sample data removed ({n} items). The homepage numbers now come from your real projects only.",
                  "success")
            return redirect(url_for("admin.settings_page") + "#numbers")
        for key in ("hero_video_url", "video_heading", "video_caption", "contact_email", "contact_phone",
                    "whatsapp_number", "announcement", "service_areas", "default_funding_days"):
            if key in f:
                set_setting(key, f.get(key, "").strip())
        for key in ("legacy_streets", "legacy_contributors"):
            if key in f:
                digits = "".join(ch for ch in f.get(key, "") if ch.isdigit())[:9]
                set_setting(key, str(int(digits or 0)))
        if "stats_mode" in f:
            set_setting("stats_mode", "exact" if f.get("stats_mode") == "exact" else "add")
        if "default_min_contribution" in f:
            set_setting("default_min_contribution_cents", str(parse_rand(f.get("default_min_contribution"), 5000)))
        if "legacy_raised" in f:
            set_setting("legacy_raised_cents", str(max(0, parse_rand(f.get("legacy_raised"), 0))))
        log_activity("settings_saved")
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings_page"))
    from .services import live_stats, public_stats
    from . import payments as pay
    pc = pay.config()
    return render_template("admin/settings.html", s=get_settings(), live=live_stats(), shown=public_stats(),
                           pay=pc, key_mask=pay.mask(pc["secret_key"]), hook_mask=pay.mask(pc["webhook_secret"]),
                           webhook_url=url_for("public.yoco_webhook", _external=True),
                           samples=count_sample_data(),
                           video_id=youtube_id(get_settings().get("hero_video_url")))


def _payment_settings(f):
    """Save, check or remove the Yoco keys from Site settings. Changing keys needs the admin's password."""
    from . import payments as pay
    action = f["action"]
    if action == "payments_check":
        ok, msg = pay.check_keys()
        flash(msg, "success" if ok else "error")
        return
    if action in ("payments_save", "payments_forget", "payments_webhook"):
        if not check_password_hash(g.admin["password_hash"], f.get("confirm_password", "")):
            flash("Enter your admin password to change payment settings.", "error")
            return
    if action == "payments_forget":
        for key in ("yoco_secret_key", "yoco_webhook_secret", "payment_mode_override"):
            set_setting(key, "")
        log_activity("payment_keys_removed")
        flash("Saved Yoco keys removed. The site now uses the keys in your hosting settings, if any.", "success")
        return
    if action == "payments_webhook":
        if not pay.config()["secret_key"]:
            flash("Save your Yoco secret key first.", "error")
            return
        try:
            data = pay.register_webhook(url_for("public.yoco_webhook", _external=True))
        except pay.PaymentError as exc:
            flash(f"Yoco didn’t set up the webhook: {exc}", "error")
            return
        secret = (data or {}).get("secret", "")
        if not secret.startswith("whsec_"):
            flash("Yoco set up the webhook but didn’t return a signing secret. Copy it from the Yoco portal.", "error")
            return
        set_setting("yoco_webhook_secret", pay.encrypt(secret))
        pay.forget_config()
        log_activity("payment_webhook_registered")
        flash("Webhook set up with Yoco and its signing secret saved.", "success")
        return
    # payments_save
    secret = "".join((f.get("yoco_secret_key") or "").split())
    hook = "".join((f.get("yoco_webhook_secret") or "").split())
    if secret and not secret.startswith(("sk_live_", "sk_test_")):
        flash("The secret key should start with sk_live_ (real payments) or sk_test_ (testing). "
              "Don’t use the public key (pk_…).", "error")
        return
    if hook and not hook.startswith("whsec_"):
        flash("The webhook signing secret should start with whsec_.", "error")
        return
    try:
        if secret:
            set_setting("yoco_secret_key", pay.encrypt(secret))
        if hook:
            set_setting("yoco_webhook_secret", pay.encrypt(hook))
    except pay.PaymentError as exc:
        flash(str(exc), "error")
        return
    pay.forget_config()
    wanted = f.get("payment_mode")
    if wanted == "yoco" and not pay.config()["secret_key"]:
        flash("Add a Yoco secret key before switching on real payments.", "error")
        return
    if wanted in ("demo", "yoco"):
        set_setting("payment_mode_override", wanted)
    pay.forget_config()
    cfg = pay.config()
    log_activity("payment_settings_saved", f"mode={cfg['mode']} live={cfg['live']}")
    if cfg["mode"] == "yoco":
        kind = "LIVE: real money" if cfg["live"] else "TEST: Yoco test cards only"
        flash(f"Payment settings saved. Payments go through Yoco ({kind}).", "success")
    else:
        flash("Payment settings saved. The site is in demo mode, so no money moves.", "success")


SAMPLE_EMAIL = "demo%@example.com"


def count_sample_data():
    try:
        return (query("SELECT COUNT(*) n FROM requests WHERE email LIKE ?", (SAMPLE_EMAIL,), one=True)["n"]
                + query("SELECT COUNT(*) n FROM sponsors WHERE name LIKE '%(sample)'", one=True)["n"])
    except Exception:  # noqa: BLE001
        return 0


def remove_sample_data():
    """Delete the demo requests, projects, contributions and sponsors added by the sample seed."""
    removed = 0
    reqs = query("SELECT id FROM requests WHERE email LIKE ?", (SAMPLE_EMAIL,))
    req_ids = [r["id"] for r in reqs]
    projects = []
    for rid in req_ids:
        projects += [r["id"] for r in query("SELECT id FROM projects WHERE request_id=?", (rid,))]
    for pid in projects:
        real = query("SELECT COUNT(*) n FROM contributions WHERE project_id=? AND email NOT LIKE ? "
                     "AND status IN ('paid','refund_pending')", (pid, SAMPLE_EMAIL), one=True)["n"]
        if real:
            continue  # a real person paid into it: keep the project
        for u in query("SELECT image FROM updates WHERE project_id=?", (pid,)):
            delete_upload(u["image"])
        execute("UPDATE requests SET project_id=NULL WHERE project_id=?", (pid,))
        execute("UPDATE projects SET parent_id=NULL WHERE parent_id=?", (pid,))
        for table in ("contributions", "updates", "project_sponsors"):
            execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        execute("DELETE FROM projects WHERE id=?", (pid,))
        removed += 1
    for rid in req_ids:
        if query("SELECT 1 FROM projects WHERE request_id=?", (rid,), one=True):
            continue
        execute("DELETE FROM quotes WHERE request_id=?", (rid,))
        execute("DELETE FROM requests WHERE id=?", (rid,))
        removed += 1
    for sp in query("SELECT * FROM sponsors WHERE name LIKE '%(sample)'"):
        delete_upload(sp["logo"])
        execute("DELETE FROM project_sponsors WHERE sponsor_id=?", (sp["id"],))
        execute("DELETE FROM sponsors WHERE id=?", (sp["id"],))
        removed += 1
    return removed


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


# ---------------------------------------------------------------- emails


@bp.route("/emails", methods=["GET", "POST"])
@login_required
def emails_list():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "check_smtp":
            ok, msg = notify.check_connection()
            flash(msg, "success" if ok else "error")
            return redirect(url_for("admin.emails_list"))
        if action == "alerts":
            raw = request.form.get("admin_notify_email", "")
            addrs = [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]
            bad = [a for a in addrs if not valid_email(a)]
            if bad:
                flash(f"Not a valid email address: {bad[0]}", "error")
            else:
                set_setting("admin_notify_email", ", ".join(addrs))
                log_activity("email_alerts_saved", ", ".join(addrs))
                flash("Team alert addresses saved.", "success")
        elif action == "toggle" and request.form.get("key") in emails.TEMPLATES:
            key = request.form["key"]
            row = emails.override(key)
            if row:
                execute("UPDATE email_templates SET enabled=?, updated_at=? WHERE key=?",
                        (0 if row["enabled"] else 1, now_iso(), key))
            else:
                d = emails.TEMPLATES[key]
                execute("INSERT INTO email_templates(key, subject, html, enabled, updated_by) VALUES (?,?,?,0,?)",
                        (key, d["subject"], d["html"], g.admin_email))
            on = emails.source(key)[2]
            log_activity("email_toggled", f"{key}: {'on' if on else 'off'}")
            flash(f"“{emails.TEMPLATES[key]['label']}” turned {'on' if on else 'off'}.", "success")
        return redirect(url_for("admin.emails_list"))
    status = request.args.get("status", "")
    rows = []
    for key, d in emails.TEMPLATES.items():
        _s, _h, enabled, custom = emails.source(key)
        rows.append(dict(d, key=key, enabled=enabled, custom=custom))
    try:
        sql, args = "SELECT * FROM email_log", ()
        if status in ("sent", "logged", "failed", "off"):
            sql, args = sql + " WHERE status=?", (status,)
        log = query(sql + " ORDER BY id DESC LIMIT 100", args)
        counts = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM email_log GROUP BY status")}
    except Exception:  # noqa: BLE001
        log, counts = [], {}
    from flask import current_app
    return render_template("admin/emails.html", templates=rows, groups=emails.GROUPS,
                           layout_custom=emails.source("layout")[3], log=log, counts=counts, status=status,
                           smtp=bool(current_app.config.get("SMTP_HOST")),
                           smtp_host=current_app.config.get("SMTP_HOST", ""),
                           smtp_port=current_app.config.get("SMTP_PORT", ""),
                           tls_verify=current_app.config.get("SMTP_TLS_VERIFY", True),
                           alerts=get_settings().get("admin_notify_email") or "",
                           env_alerts=current_app.config.get("ADMIN_NOTIFY_EMAIL", ""))


def _email_meta(key):
    if key == "layout":
        return {"label": "Layout (header and footer)", "group": "All emails",
                "when": "Wraps every email. Put {{ content }} where each message should appear.",
                "vars": emails.COMMON_VARS + [("content", "The message itself (required)"),
                                              ("subject", "The email's subject"),
                                              ("preheader", "Preview text shown in the inbox")],
                "subject": "", "html": emails.LAYOUT}
    if key not in emails.TEMPLATES:
        abort(404)
    d = emails.TEMPLATES[key]
    return dict(d, vars=emails.COMMON_VARS + list(dict.fromkeys(d["vars"])))


@bp.route("/emails/<key>", methods=["GET", "POST"])
@login_required
def email_edit(key):
    meta = _email_meta(key)
    subject, html, enabled, custom = emails.source(key)
    if request.method == "POST":
        action = request.form.get("action", "save")
        new_subject = clean_text(request.form.get("subject"), 200) if key != "layout" else ""
        new_html = (request.form.get("html") or "").replace("\r\n", "\n")
        if action == "reset":
            execute("DELETE FROM email_templates WHERE key=?", (key,))
            log_activity("email_reset", key)
            flash("Back to the original wording and design.", "success")
            return redirect(url_for("admin.email_edit", key=key))
        error = emails.validate(new_subject, new_html, key)
        if key != "layout" and not new_subject:
            error = "The subject can’t be empty."
        if error:
            flash(error, "error")
            return render_template("admin/email_edit.html", key=key, meta=meta, subject=new_subject, html=new_html,
                                   enabled=enabled, custom=custom), 400
        if action == "test":
            ctx = emails.sample_context(key if key != "layout" else "request_received")
            if key == "layout":
                d = emails.TEMPLATES["request_received"]
                parts = emails.render_parts(d["subject"], d["html"], new_html, ctx)
            else:
                parts = emails.render_parts(new_subject, new_html, emails.source("layout")[1], ctx)
            ok, problem = notify.deliver_now(g.admin_email, key, "[Test] " + parts[0], parts[1], parts[2])
            if ok:
                flash(f"Test email sent to {g.admin_email} using sample details. It hasn’t been saved yet.", "success")
            else:
                flash(problem, "error")
            return render_template("admin/email_edit.html", key=key, meta=meta, subject=new_subject, html=new_html,
                                   enabled=enabled, custom=custom)
        if emails.override(key):
            execute("UPDATE email_templates SET subject=?, html=?, updated_at=?, updated_by=? WHERE key=?",
                    (new_subject, new_html, now_iso(), g.admin_email, key))
        else:
            execute("INSERT INTO email_templates(key, subject, html, enabled, updated_by) VALUES (?,?,?,1,?)",
                    (key, new_subject, new_html, g.admin_email))
        log_activity("email_saved", key)
        flash("Email saved. It will be used from now on.", "success")
        return redirect(url_for("admin.email_edit", key=key))
    return render_template("admin/email_edit.html", key=key, meta=meta, subject=subject, html=html,
                           enabled=enabled, custom=custom)


@bp.route("/emails/<key>/preview", methods=["GET", "POST"])
@login_required
def email_preview(key):
    _email_meta(key)
    if request.method == "POST":
        subject_src = request.form.get("subject", "")
        html_src = request.form.get("html", "")
    else:
        subject_src, html_src = emails.source(key)[:2]
    try:
        if key == "layout":
            d = emails.TEMPLATES["request_received"]
            subject, full, _t = emails.render_parts(d["subject"], d["html"], html_src,
                                                    emails.sample_context("request_received"))
        else:
            subject, full, _t = emails.render_parts(subject_src, html_src, emails.source("layout")[1],
                                                    emails.sample_context(key))
        body = (f'<div style="font:13px Arial,sans-serif;background:#fff;border-bottom:1px solid #dfe2e8;'
                f'padding:10px 16px;color:#5d6585">Subject: <b style="color:#1f2a5c">{escape(subject)}</b></div>' + full)
    except Exception as exc:  # noqa: BLE001
        body = (f'<div style="font:15px Arial,sans-serif;padding:24px;color:#b4232a">'
                f'<b>This template has a problem</b><br>{escape(str(exc))}</div>')
    resp = Response(body, mimetype="text/html")
    # The preview shows admin-written HTML: no scripts, no forms, only images and inline styles.
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; img-src https: http: data:; "
                                               "style-src 'unsafe-inline'; frame-ancestors 'self'; sandbox")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- About page photo story


def _story_rows():
    return query("SELECT * FROM story_photos ORDER BY sort_order, id")


def _renumber(ids):
    for i, sid in enumerate(ids):
        execute("UPDATE story_photos SET sort_order=? WHERE id=?", (i, sid))


@bp.route("/story", methods=["GET", "POST"])
@login_required
def story():
    if request.method == "POST":
        f = request.form
        rows = _story_rows()
        action = f.get("action", "save")

        if action == "upload":
            files = [fs for fs in request.files.getlist("photos") if fs and fs.filename][:30]
            if not files:
                flash("Choose at least one photo.", "error")
                return redirect(url_for("admin.story"))
            caption = clean_text(f.get("caption"), 240)
            where = f.get("where", "end")
            new_ids = []
            for fs in files:
                try:
                    name = save_image(fs, prefix="story")
                except ValueError as exc:
                    flash(f"{fs.filename}: {exc}", "error")
                    continue
                new_ids.append(execute("INSERT INTO story_photos(image, caption, sort_order) VALUES (?,?,?)",
                                       (name, caption if len(files) == 1 else "", 10_000 + len(new_ids))))
            old = [r["id"] for r in rows]
            if where == "start":
                order = new_ids + old
            elif where.isdigit() and 1 <= int(where) <= len(old) + 1:
                pos = int(where) - 1
                order = old[:pos] + new_ids + old[pos:]
            else:
                order = old + new_ids
            _renumber(order)
            if new_ids:
                log_activity("story_photos_added", str(len(new_ids)))
                flash(f"{len(new_ids)} photo{'s' if len(new_ids) != 1 else ''} added. Write a short note under each one and save.",
                      "success")
            return redirect(url_for("admin.story") + "#photos")

        if action == "delete":
            row = query("SELECT * FROM story_photos WHERE id=?", (f.get("photo_id"),), one=True)
            if row:
                if not row["image"].startswith("static:"):
                    delete_upload(row["image"])
                execute("DELETE FROM story_photos WHERE id=?", (row["id"],))
                _renumber([r["id"] for r in _story_rows()])
                log_activity("story_photo_deleted", str(row["id"]))
                flash("Photo removed from the story.", "success")
            return redirect(url_for("admin.story") + "#photos")

        # save notes and order (also used by the move buttons)
        keyed = []
        for i, r in enumerate(rows):
            execute("UPDATE story_photos SET caption=? WHERE id=?",
                    (clean_text(f.get(f"caption_{r['id']}", r["caption"]), 240), r["id"]))
            try:
                pos = float(f.get(f"pos_{r['id']}") or i + 1)
            except ValueError:
                pos = i + 1
            keyed.append([pos, i, r["id"]])
        keyed.sort()
        order = [k[2] for k in keyed]
        move = f.get("move", "")
        if ":" in move:
            how, _, sid = move.partition(":")
            sid = int(sid) if sid.isdigit() else -1
            if sid in order:
                i = order.index(sid)
                order.pop(i)
                j = {"up": max(0, i - 1), "down": min(len(order), i + 1), "top": 0}.get(how, len(order))
                order.insert(j, sid)
        _renumber(order)
        log_activity("story_saved", f"{len(order)} photos")
        flash("Story saved.", "success")
        return redirect(url_for("admin.story") + "#photos")
    return render_template("admin/story.html", rows=_story_rows())


# ---------------------------------------------------------------- page content (About, Sponsors)


@bp.route("/content/<page>", methods=["GET", "POST"])
@login_required
def page_content(page):
    from . import content
    if page not in content.PAGES:
        abort(404)
    spec = content.PAGES[page]
    if request.method == "POST":
        f = request.form
        reset = f.get("reset_section")
        changed = 0
        for idx, (title, fields) in enumerate(spec["sections"]):
            for key, _label, kind, default in fields:
                if reset is not None and reset == str(idx):
                    old = content.get(key)
                    content.save(key, default)
                    if kind == content.IMG:
                        _drop_content_image(old, default)
                    continue
                if reset is not None:
                    continue
                if kind == content.IMG:
                    fs = request.files.get(key)
                    if f.get(f"{key}__default"):
                        old = content.get(key)
                        content.save(key, default)
                        _drop_content_image(old, default)
                    elif fs and fs.filename:
                        try:
                            name = save_image(fs, prefix="page")
                        except ValueError as exc:
                            flash(f"{title}: {exc}", "error")
                            continue
                        old = content.get(key)
                        content.save(key, name)
                        _drop_content_image(old, name)
                        changed += 1
                    continue
                if key in f:
                    before = content.get(key)
                    content.save(key, f.get(key, ""))
                    changed += int(content.get(key) != before)
        log_activity("page_content_saved", page)
        flash("Section reset to the original wording." if reset is not None else "Page saved.", "success")
        return redirect(url_for("admin.page_content", page=page) + (f"#s{reset}" if reset is not None else ""))
    return render_template("admin/page_content.html", page=page, spec=spec, pages=content.PAGES,
                           values=content.all_values(), custom=content.customised())


def _drop_content_image(old, new=None):
    if old and old != new and not str(old).startswith("static:"):
        delete_upload(old)


# ---------------------------------------------------------------- About page layout: show, hide, order, add blocks


@bp.route("/about-layout", methods=["GET", "POST"])
@login_required
def about_layout():
    from . import content
    if request.method == "POST":
        f = request.form
        action = f.get("action", "save")
        if action == "add_block":
            bid = _save_block(None, f)
            if bid:
                log_activity("about_block_added", str(bid))
                flash("Block added at the bottom of the page. Move it where you want it.", "success")
            return redirect(url_for("admin.about_layout") + "#layout")
        items = content.about_layout()
        keyed = []
        for i, item in enumerate(items):
            item["on"] = bool(f.get(f"on_{item['id']}"))
            try:
                pos = float(f.get(f"pos_{item['id']}") or i + 1)
            except ValueError:
                pos = i + 1
            keyed.append((pos, i, item))
        keyed.sort(key=lambda k: (k[0], k[1]))
        items = [k[2] for k in keyed]
        how, _, target = f.get("move", "").partition("|")
        ids = [i["id"] for i in items]
        if target in ids:
            i = ids.index(target)
            item = items.pop(i)
            items.insert(max(0, i - 1) if how == "up" else min(len(items), i + 1), item)
        content.save_layout(items)
        shown = [k for k, _ in content.STATS if f.get(f"stat_{k}")]
        execute("INSERT INTO page_content(key, value, updated_at) VALUES ('about.stats_show', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (",".join(shown), now_iso()))
        log_activity("about_layout_saved")
        flash("Layout saved.", "success")
        return redirect(url_for("admin.about_layout") + "#layout")
    return render_template("admin/about_layout.html", layout=content.about_layout(), stats=content.STATS,
                           stats_shown=content.stats_shown())


def _save_block(block, f):
    heading = clean_text(f.get("heading"), 160)
    body = clean_text(f.get("body"), 4000, multiline=True)
    if not heading and not body:
        flash("Give the block a heading or some text.", "error")
        return None
    image = block["image"] if block else None
    if request.files.get("image") and request.files["image"].filename:
        try:
            new = save_image(request.files["image"], prefix="page")
        except ValueError as exc:
            flash(str(exc), "error")
            return None
        if image:
            delete_upload(image)
        image = new
    if f.get("remove_image") and image:
        delete_upload(image)
        image = None
    data = (heading, body, image, "right" if f.get("image_side") == "right" else "left",
            1 if f.get("shaded") else 0, clean_text(f.get("button_text"), 60), safe_url(f.get("button_url"), allow_relative=True))
    if block:
        execute("UPDATE page_blocks SET heading=?, body=?, image=?, image_side=?, shaded=?, button_text=?, button_url=? "
                "WHERE id=?", data + (block["id"],))
        return block["id"]
    return execute("INSERT INTO page_blocks(page, heading, body, image, image_side, shaded, button_text, button_url) "
                   "VALUES ('about',?,?,?,?,?,?,?)", data)


@bp.route("/about-layout/block/<int:bid>", methods=["GET", "POST"])
@login_required
def about_block(bid):
    block = query("SELECT * FROM page_blocks WHERE id=?", (bid,), one=True)
    if not block:
        abort(404)
    if request.method == "POST":
        if request.form.get("action") == "delete":
            delete_upload(block["image"])
            execute("DELETE FROM page_blocks WHERE id=?", (bid,))
            log_activity("about_block_deleted", str(bid))
            flash("Block removed from the About page.", "success")
            return redirect(url_for("admin.about_layout") + "#layout")
        if _save_block(block, request.form):
            log_activity("about_block_saved", str(bid))
            flash("Block saved.", "success")
            return redirect(url_for("admin.about_layout") + "#layout")
        return redirect(url_for("admin.about_block", bid=bid))
    return render_template("admin/about_block.html", b=block)
