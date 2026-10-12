"""Domain logic shared by the public site, the admin portal and background tasks."""
from datetime import timedelta

from flask import current_app, url_for

from . import notify, payments
from .db import execute, get_settings, log_activity, now_iso, query
from .emails import first_name, project_context, request_context
from .utils import days_left, load_json, make_token, rand, slugify, today_sast

# Public-facing project lifecycle, used by the status bar.
PHASES = [
    ("requested", "Requested"),
    ("approved", "Approved"),
    ("funding", "Funding"),
    ("funded", "Funded"),
    ("in_progress", "Cleaning"),
    ("completed", "Completed"),
]
STATUS_TO_PHASE = {
    "funding": 2,
    "funded": 3,
    "scheduled": 3,
    "in_progress": 4,
    "completed": 5,
}
STATUS_LABELS = {
    "funding": "Raising funds",
    "funded": "Funded",
    "scheduled": "Clean scheduled",
    "in_progress": "Cleaning in progress",
    "completed": "Completed",
    "failed": "Goal not reached — refunded",
    "cancelled": "Cancelled — refunded",
}
REQUEST_STATUS_LABELS = {
    "submitted": "Submitted",
    "under_review": "Under review",
    "revision_requested": "Changes requested",
    "approved": "Approved",
    "rejected": "Not approved",
}
OPEN_STATUSES = ("funding", "funded", "scheduled", "in_progress")


# ---------------------------------------------------------------- projects


def project_view(row):
    """Return a dict of a project row enriched with funding totals and display fields."""
    p = dict(row)
    totals = query(
        "SELECT COALESCE(SUM(amount_cents),0) AS raised, COUNT(*) AS backers "
        "FROM contributions WHERE project_id=? AND status='paid'",
        (p["id"],),
        one=True,
    )
    p["raised_cents"] = totals["raised"]
    p["backers"] = totals["backers"]
    goal = p["goal_cents"] or 1
    p["pct"] = min(100, round(p["raised_cents"] * 100 / goal))
    p["remaining_cents"] = max(0, p["goal_cents"] - p["raised_cents"])
    p["days_left"] = days_left(p["deadline"])
    p["status_label"] = STATUS_LABELS.get(p["status"], p["status"].replace("_", " ").title())
    p["phase_index"] = STATUS_TO_PHASE.get(p["status"], -1)
    p["line_items_list"] = load_json(p.get("line_items"), [])
    p["location"] = ", ".join(x for x in (p["suburb"], p["city"]) if x)
    p["is_open_for_funding"] = p["status"] == "funding" and p["days_left"] >= 0
    # what we make if the goal is reached: charge excl. VAT, less our cost and card fees
    vat = float(p.get("vat_pct") or 0)
    fee = float(p.get("fee_pct") or 0)
    excl = int(round(p["goal_cents"] / (1 + vat / 100)))
    p["fees_cents"] = int(round(p["goal_cents"] * fee / 100))
    p["profit_cents"] = excl - int(p.get("budget_cents") or 0) - p["fees_cents"]
    p["margin_pct"] = round(p["profit_cents"] * 100 / excl, 1) if excl else 0.0
    return p


def get_project_by_slug(slug):
    row = query("SELECT * FROM projects WHERE slug=?", (slug,), one=True)
    return project_view(row) if row else None


def get_project(project_id):
    row = query("SELECT * FROM projects WHERE id=?", (project_id,), one=True)
    return project_view(row) if row else None


def unique_slug(base):
    slug, n = base, 2
    while query("SELECT 1 FROM projects WHERE slug=?", (slug,), one=True):
        slug, n = f"{base}-{n}", n + 1
    return slug


def live_stats():
    """Totals worked out from the projects and payments on this site."""
    return {
        "streets": query("SELECT COUNT(*) AS n FROM projects WHERE status='completed'", one=True)["n"],
        "raised_cents": query("SELECT COALESCE(SUM(amount_cents),0) AS n FROM contributions WHERE status='paid'",
                              one=True)["n"],
        "contributors": query("SELECT COUNT(DISTINCT lower(email)) AS n FROM contributions WHERE status='paid'",
                              one=True)["n"],
        "active": query("SELECT COUNT(*) AS n FROM projects WHERE status='funding'", one=True)["n"],
    }


def _int(value):
    try:
        return max(0, int(float(str(value or 0).strip() or 0)))
    except ValueError:
        return 0


def public_stats():
    """Numbers shown on the homepage and About page.

    "add" (default): live totals plus the work done before the platform, from Site settings.
    "exact": show the Site settings figures as they are.
    """
    s = get_settings()
    live = live_stats()
    entered = {"streets": _int(s.get("legacy_streets")), "raised_cents": _int(s.get("legacy_raised_cents")),
               "contributors": _int(s.get("legacy_contributors"))}
    if s.get("stats_mode") == "exact":
        return dict(entered, active=live["active"])
    return {k: live[k] + entered[k] for k in entered} | {"active": live["active"]}


def create_project_from_request(req, quote, *, service_type, goal_cents, budget_cents,
                                min_contribution_cents, funding_days, title, summary, cover_image=None):
    deadline = (today_sast() + timedelta(days=int(funding_days))).isoformat()
    slug = unique_slug(slugify(f"{req['street_name']} {req['suburb'] or req['city']}"))
    project_id = execute(
        """INSERT INTO projects(slug, request_id, title, street_name, suburb, city, summary, scope,
               service_type, line_items, budget_cents, goal_cents, min_contribution_cents, deadline,
               status, cover_image)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'funding', ?)""",
        (slug, req["id"], title, req["street_name"], req["suburb"], req["city"], summary,
         quote["scope"] if quote else "", service_type, quote["line_items"] if quote else "[]",
         budget_cents, goal_cents, min_contribution_cents, deadline, cover_image or req["photo"]),
    )
    execute("UPDATE requests SET status='approved', project_id=?, updated_at=? WHERE id=?",
            (project_id, now_iso(), req["id"]))
    return project_id


def open_next_monthly_round(project):
    """Monthly service: open a fresh funding round that copies the previous one."""
    funding_days = int(get_settings().get("default_funding_days") or 21)
    deadline = (today_sast() + timedelta(days=funding_days)).isoformat()
    round_no = project["round_no"] + 1
    slug = unique_slug(f"{project['slug'].rsplit('-round-', 1)[0]}-round-{round_no}")
    return execute(
        """INSERT INTO projects(slug, request_id, parent_id, round_no, title, street_name, suburb, city,
               summary, scope, service_type, line_items, budget_cents, goal_cents, min_contribution_cents,
               deadline, status, cover_image)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'monthly', ?,?,?,?,?, 'funding', ?)""",
        (slug, project["request_id"], project["id"], round_no,
         project["title"].split(" — Month")[0] + f" — Month {round_no}",
         project["street_name"], project["suburb"], project["city"], project["summary"], project["scope"],
         project["line_items"], project["budget_cents"], project["goal_cents"],
         project["min_contribution_cents"], deadline, project["cover_image"]),
    )


# ---------------------------------------------------------------- contributions


def mark_contribution_paid(contribution_id, payment_id=None):
    """Idempotently mark a contribution paid, then check whether the goal is met."""
    c = query("SELECT * FROM contributions WHERE id=?", (contribution_id,), one=True)
    if not c or c["status"] == "paid":
        return False
    if c["status"] not in ("pending", "failed"):
        return False
    execute("UPDATE contributions SET status='paid', payment_id=COALESCE(?, payment_id), paid_at=? WHERE id=?",
            (payment_id, now_iso(), contribution_id))
    project = get_project(c["project_id"])
    notify.send(c["email"], "contribution_receipt", "project", project["id"], name=first_name(c["name"]),
                amount=rand(c["amount_cents"]), contribution_id=c["id"], **project_context(project))

    # A contribution that lands after a project closed is refunded straight away.
    if project["status"] in ("failed", "cancelled"):
        refund_contribution(contribution_id, reason="Project already closed")
        return True

    if project["status"] == "funding" and project["raised_cents"] >= project["goal_cents"]:
        execute("UPDATE projects SET status='funded', funded_at=?, updated_at=? WHERE id=?",
                (now_iso(), now_iso(), project["id"]))
        log_activity("project_funded", project["title"], admin_email="system")
        project = get_project(project["id"])
        notify.notify_admins("admin_funded", "project", project["id"], **project_context(project))
        email_followers(project["id"], "project_funded")
    return True


def refund_contribution(contribution_id, reason=""):
    c = query("SELECT * FROM contributions WHERE id=?", (contribution_id,), one=True)
    if not c or c["status"] != "paid":
        return False
    try:
        status, refund_id = payments.refund_checkout(c["checkout_id"], reason=reason)
    except payments.PaymentError:
        current_app.logger.exception("Refund failed for contribution %s", contribution_id)
        execute("UPDATE contributions SET status='refund_pending' WHERE id=?", (contribution_id,))
        return False
    new_status = "refunded" if status == "succeeded" else "refund_pending"
    execute("UPDATE contributions SET status=?, refund_id=?, refunded_at=? WHERE id=?",
            (new_status, refund_id, now_iso() if new_status == "refunded" else None, contribution_id))
    project = get_project(c["project_id"])
    notify.send(c["email"], "refund", "project", project["id"], name=first_name(c["name"]),
                amount=rand(c["amount_cents"]), contribution_id=c["id"], reason=reason, **project_context(project))
    return True


def close_and_refund(project_id, status, reason):
    """Close a project as failed/cancelled and refund every paid contribution."""
    execute("UPDATE projects SET status=?, closed_reason=?, updated_at=? WHERE id=?",
            (status, reason, now_iso(), project_id))
    paid = query("SELECT id FROM contributions WHERE project_id=? AND status IN ('paid','refund_pending')",
                 (project_id,))
    ok = 0
    for row in paid:
        execute("UPDATE contributions SET status='paid' WHERE id=? AND status='refund_pending'", (row["id"],))
        if refund_contribution(row["id"], reason=reason):
            ok += 1
    log_activity(f"project_{status}", f"project {project_id}: {reason} — {ok}/{len(paid)} refunds issued",
                 admin_email="system")
    project = get_project(project_id)
    req = requester_for(project)
    if project and req:
        notify.send(req["email"], "project_closed", "project", project_id, reason=reason,
                    **dict(project_context(project), **request_context(req)))
    return ok, len(paid)


def process_expired_projects():
    """Close projects whose deadline passed without reaching the goal, and refund backers.

    Safe to run repeatedly (cron, admin dashboard load). Also retries refunds that
    previously failed.
    """
    results = []
    today = today_sast().isoformat()
    rows = query("SELECT * FROM projects WHERE status='funding' AND deadline < ?", (today,))
    for row in rows:
        p = project_view(row)
        if p["raised_cents"] >= p["goal_cents"]:
            execute("UPDATE projects SET status='funded', funded_at=? WHERE id=?", (now_iso(), p["id"]))
            results.append((p["title"], "funded"))
            continue
        ok, total = close_and_refund(p["id"], "failed", "Funding goal not reached by the deadline")
        results.append((p["title"], f"failed, {ok}/{total} refunded"))
    for row in query("SELECT id FROM contributions WHERE status='refund_pending'"):
        execute("UPDATE contributions SET status='paid' WHERE id=?", (row["id"],))
        refund_contribution(row["id"], reason="Retrying refund")
    send_deadline_reminders()
    return results


def send_deadline_reminders(days=3):
    """Nudge the requester once when a street is close to its deadline and not yet funded."""
    sent = 0
    for row in query("SELECT * FROM projects WHERE status='funding' AND COALESCE(reminder_sent,0)=0"):
        p = project_view(row)
        if not (0 <= p["days_left"] <= days) or p["raised_cents"] >= p["goal_cents"]:
            continue
        execute("UPDATE projects SET reminder_sent=1 WHERE id=?", (p["id"],))
        req = requester_for(p)
        if req and notify.send(req["email"], "deadline_reminder", "project", p["id"],
                               **dict(project_context(p), **request_context(req))):
            sent += 1
    return sent


def requester_for(project):
    if not project or not project.get("request_id"):
        return None
    return query("SELECT * FROM requests WHERE id=?", (project["request_id"],), one=True)


def followers(project_id):
    """Everyone who should hear about a project: paid backers plus the person who asked for it."""
    project = get_project(project_id)
    people, seen = [], set()
    req = requester_for(project)
    if req:
        people.append((req["email"], req["requester_name"]))
    people += [(c["email"], c["name"]) for c in query(
        "SELECT email, name FROM contributions WHERE project_id=? AND status='paid' ORDER BY id", (project_id,))]
    out = []
    for email, name in people:
        key = (email or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append((email.strip(), name))
    return project, out


def email_followers(project_id, key, **extra):
    project, people = followers(project_id)
    if not project:
        return 0
    ctx = project_context(project)
    return notify.send_many(key, [(email, dict(ctx, name=first_name(name), **extra)) for email, name in people],
                            "project", project_id)


def email_backers(project_id, key, **extra):  # backwards-compatible name
    return email_followers(project_id, key, **extra)


def project_url(project):
    try:
        return url_for("public.project", slug=project["slug"], _external=True)
    except RuntimeError:
        return f"/projects/{project['slug']}"


def new_return_token():
    return make_token()
