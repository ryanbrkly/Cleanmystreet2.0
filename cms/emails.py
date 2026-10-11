"""Email templates that the team can edit in the admin portal.

Every email the site sends is defined here with a default subject and HTML body.
Admins can override the subject and HTML (Admin → Emails); overrides are saved in
the `email_templates` table and the defaults below are used otherwise.

Templates use Jinja syntax ({{ name }}, {% if note %}…{% endif %}) and are rendered
in a sandbox, so an edited template cannot reach the server. Values are escaped
automatically. Two helpers are available in every template:

    {{ button(project_url, "See your street") }}   a big tappable link button
    {{ progress(pct) }}                             a funding progress bar

Each email is wrapped in the shared "layout" template, which places the body
where {{ content }} appears.
"""
import html as htmlmod
import re
from datetime import datetime

from flask import current_app, has_request_context, url_for
from jinja2 import ChainableUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from .db import execute, get_settings, now_iso, query
from .utils import nice_date, rand

MAX_TEMPLATE_CHARS = 60000

INK = "#1f2a5c"
ROYAL = "#0a3a9e"
H1 = f'style="margin:0 0 16px;font-size:24px;line-height:1.25;color:{INK}"'
P = 'style="margin:0 0 16px"'
BOX = 'style="background:#f2f3f0;border-radius:8px;padding:16px 20px;margin:0 0 20px"'
SMALL = 'style="margin:0 0 16px;font-size:13px;color:#5d6585"'

# ---------------------------------------------------------------- variables

COMMON_VARS = [
    ("site_name", "Clean My Street"),
    ("site_url", "Website address"),
    ("contact_email", "Your contact email (Site settings)"),
    ("contact_phone", "Your contact phone (Site settings)"),
]
REQUEST_VARS = [
    ("name", "Requester's first name"),
    ("full_name", "Requester's full name"),
    ("ref", "Request reference, e.g. CMS-7K2Q"),
    ("street", "Street name"),
    ("location", "Suburb and city"),
    ("track_url", "Private tracking link for the request"),
    ("note", "Message typed by the admin (revision or reason)"),
]
PROJECT_VARS = [
    ("project_title", "Project title"),
    ("project_url", "Public project page"),
    ("location", "Suburb and city"),
    ("goal", "Funding goal, e.g. R18 500"),
    ("raised", "Amount raised so far"),
    ("remaining", "Amount still needed"),
    ("pct", "Percent funded (number)"),
    ("backers", "Number of contributors"),
    ("deadline", "Funding deadline, e.g. 23 Oct 2026"),
    ("days_left", "Days until the deadline"),
    ("min_contribution", "Minimum contribution"),
    ("service", "once-off or monthly"),
    ("scheduled_for", "Clean date, if set"),
]
PERSON_VARS = [("name", "Recipient's first name")]
CONTRIBUTION_VARS = [("amount", "Contribution amount"), ("contribution_id", "Contribution number")]
UPDATE_VARS = [
    ("update_title", "Title of the update"),
    ("update_body", "Text of the update"),
    ("update_image_url", "Photo in the update (if any)"),
    ("update_video_url", "YouTube link in the update (if any)"),
]


def _btn(url, label):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 24px"><tr>'
            f'<td style="background:{ROYAL};border-radius:999px">'
            f'<a href="{escape(url)}" style="display:inline-block;padding:14px 26px;color:#ffffff;font-weight:700;'
            f'text-decoration:none;font-size:16px">{escape(label)}</a></td></tr></table>')


def _bar(pct):
    try:
        pct = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        pct = 0
    fill = f'<td width="{pct}%" style="background:#d6f43c;height:12px;font-size:0;line-height:0">&nbsp;</td>' if pct else ""
    rest = f'<td style="background:#dfe2e8;height:12px;font-size:0;line-height:0">&nbsp;</td>' if pct < 100 else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-radius:6px;overflow:hidden;margin:0 0 8px"><tr>{fill}{rest}</tr></table>')


# ---------------------------------------------------------------- defaults

LAYOUT = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{{{ subject }}}}</title></head>
<body style="margin:0;padding:0;background:#f2f3f0">
<div style="display:none;max-height:0;overflow:hidden">{{{{ preheader }}}}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f3f0">
  <tr><td align="center" style="padding:24px 12px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden">
      <tr><td style="background:{INK};padding:20px 28px">
        <a href="{{{{ site_url }}}}" style="text-decoration:none;color:#ffffff;font:800 20px/1 Arial,sans-serif;letter-spacing:.02em">CLEAN MY <span style="color:#d6f43c">STREET</span></a>
      </td></tr>
      <tr><td style="height:6px;background:repeating-linear-gradient(135deg,#d6f43c 0 14px,#c9cfd6 14px 28px);background-color:#d6f43c;font-size:0;line-height:0">&nbsp;</td></tr>
      <tr><td style="padding:32px 28px 12px;font:16px/1.6 Arial,Helvetica,sans-serif;color:{INK}">
        {{{{ content }}}}
      </td></tr>
      <tr><td style="padding:20px 28px 28px;border-top:1px solid #dfe2e8;font:13px/1.6 Arial,Helvetica,sans-serif;color:#5d6585">
        Clean My Street (Pty) Ltd · Neighbours funding clean streets.<br>
        Questions? Reply to this email or write to <a href="mailto:{{{{ contact_email }}}}" style="color:{ROYAL}">{{{{ contact_email }}}}</a>{{% if contact_phone %}} · {{{{ contact_phone }}}}{{% endif %}}.<br>
        You’re getting this because you asked for a clean or backed a street on <a href="{{{{ site_url }}}}" style="color:{ROYAL}">cleanmystreet.co.za</a>.
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

FUNDING_BOX = f"""<div {BOX}>
  {{{{ progress(pct) }}}}
  <p style="margin:0"><b>{{{{ raised }}}}</b> of {{{{ goal }}}} raised · {{{{ backers }}}} neighbour{{{{ '' if backers == 1 else 's' }}}}{{% if deadline %}} · closes {{{{ deadline }}}}{{% endif %}}</p>
</div>"""

TEMPLATES = {
    # ---- requester
    "request_received": dict(
        group="Requester", label="Request received", when="Someone submits a cleaning request.",
        vars=REQUEST_VARS,
        subject="We’ve received your request ({{ ref }})",
        html=f"""<h1 {H1}>Thanks, {{{{ name }}}}. We’ve got your request.</h1>
<p {P}>We’ll assess <b>{{{{ street }}}}</b> and send you a fixed, itemised quote within 48 hours.</p>
<div {BOX}><p style="margin:0">Your reference: <b>{{{{ ref }}}}</b></p></div>
<p {P}>You can check progress at any time on your private tracking page. Keep this link to yourself, as it lets you edit your request.</p>
{{{{ button(track_url, "Track your request") }}}}
<p {SMALL}>What happens next: we quote, you share the funding link with your neighbours, and once the goal is reached our crew books the clean. If the goal isn’t reached by the deadline, everyone is refunded in full.</p>"""),
    "request_updated": dict(
        group="Requester", label="Request changes received", when="The requester edits their request after you asked for changes.",
        vars=REQUEST_VARS,
        subject="Thanks, we’ve got your changes ({{ ref }})",
        html=f"""<h1 {H1}>Your changes are with our team</h1>
<p {P}>Hi {{{{ name }}}}, thanks for updating your request for <b>{{{{ street }}}}</b>. We’ll review it and come back to you with a quote.</p>
{{{{ button(track_url, "View your request") }}}}"""),
    "request_revision": dict(
        group="Requester", label="More details needed", when="You click “Request changes” on a request.",
        vars=REQUEST_VARS,
        subject="We need a few more details ({{ ref }})",
        html=f"""<h1 {H1}>A few more details, please</h1>
<p {P}>Hi {{{{ name }}}}, before we can quote for <b>{{{{ street }}}}</b> we need a little more information:</p>
<div {BOX}><p style="margin:0;white-space:pre-line">{{{{ note }}}}</p></div>
{{{{ button(track_url, "Update your request") }}}}
<p {SMALL}>It only takes a minute. Your request stays open until you reply.</p>"""),
    "request_approved": dict(
        group="Requester", label="Quote approved, funding page live", when="You approve a request and publish the project.",
        vars=REQUEST_VARS + PROJECT_VARS,
        subject="Your funding page for {{ street }} is live",
        html=f"""<h1 {H1}>Good news, {{{{ name }}}}: your street is live</h1>
<p {P}>Your quote for <b>{{{{ street }}}}</b> is approved and the funding page is ready to share.</p>
<div {BOX}>
  <p style="margin:0 0 6px">Funding goal: <b>{{{{ goal }}}}</b>{{% if service == 'monthly' %}} per month{{% endif %}}</p>
  <p style="margin:0 0 6px">Minimum per neighbour: <b>{{{{ min_contribution }}}}</b></p>
  <p style="margin:0">Deadline: <b>{{{{ deadline }}}}</b></p>
</div>
<p {P}>Share the link in your street or estate WhatsApp group. Most streets are funded by 10 to 20 neighbours.</p>
{{{{ button(project_url, "Open your funding page") }}}}
<p {SMALL}>If the goal isn’t reached by {{{{ deadline }}}}, everyone is refunded in full automatically. Track everything here: <a href="{{{{ track_url }}}}" style="color:{ROYAL}">your request</a>.</p>"""),
    "request_rejected": dict(
        group="Requester", label="Request declined", when="You reject a request.",
        vars=REQUEST_VARS,
        subject="Update on your request {{ ref }}",
        html=f"""<h1 {H1}>About your request for {{{{ street }}}}</h1>
<p {P}>Hi {{{{ name }}}}, thank you for asking us to help. Unfortunately we can’t take this one on at the moment.</p>
{{% if note %}}<div {BOX}><p style="margin:0;white-space:pre-line">{{{{ note }}}}</p></div>{{% endif %}}
<p {P}>If anything changes, you’re welcome to send a new request any time.</p>
{{{{ button(track_url, "View your request") }}}}"""),
    "deadline_reminder": dict(
        group="Requester", label="Deadline reminder", when="Three days before the deadline, if the street isn’t funded yet. Sent once.",
        vars=REQUEST_VARS + PROJECT_VARS,
        subject="{{ days_left }} days left to fund {{ street }}",
        html=f"""<h1 {H1}>{{{{ days_left }}}} days to go, {{{{ remaining }}}} still needed</h1>
<p {P}>Hi {{{{ name }}}}, your street is getting close. A quick reminder in your WhatsApp group usually makes the difference.</p>
{FUNDING_BOX}
{{{{ button(project_url, "Share the funding page") }}}}
<p {SMALL}>If the goal isn’t reached by {{{{ deadline }}}}, everyone is refunded in full automatically.</p>"""),
    "project_closed": dict(
        group="Requester", label="Project closed, backers refunded", when="A project misses its goal or you cancel it.",
        vars=REQUEST_VARS + PROJECT_VARS + [("reason", "Why it closed")],
        subject="{{ project_title }} has closed",
        html=f"""<h1 {H1}>{{{{ project_title }}}} has closed</h1>
<p {P}>Hi {{{{ name }}}}, the funding page for <b>{{{{ street }}}}</b> has closed{{% if reason %}} ({{{{ reason|lower }}}}){{% endif %}}. Everyone who contributed is being refunded in full to the card they paid with.</p>
{FUNDING_BOX}
<p {P}>You’re welcome to try again. Reply to this email and we’ll help you plan a new round, for example with a smaller first clean.</p>"""),
    # ---- backers and requester (followers)
    "contribution_receipt": dict(
        group="Backers", label="Contribution receipt", when="A payment succeeds.",
        vars=PERSON_VARS + PROJECT_VARS + CONTRIBUTION_VARS,
        subject="Thank you for backing {{ project_title }}",
        html=f"""<h1 {H1}>Thank you, {{{{ name }}}}!</h1>
<p {P}>Your contribution of <b>{{{{ amount }}}}</b> to <b>{{{{ project_title }}}}</b> has been received.</p>
{FUNDING_BOX}
<p {P}>Your money is held for this street only. If the goal isn’t reached by {{{{ deadline }}}}, you’ll be refunded in full automatically.</p>
{{{{ button(project_url, "Follow the project") }}}}
<p {SMALL}>Receipt reference: contribution #{{{{ contribution_id }}}}</p>"""),
    "project_funded": dict(
        group="Backers", label="Street fully funded", when="A project reaches its goal. Goes to backers and the requester.",
        vars=PERSON_VARS + PROJECT_VARS,
        subject="{{ project_title }} is fully funded!",
        html=f"""<h1 {H1}>You did it: {{{{ project_title }}}} is funded</h1>
<p {P}>Hi {{{{ name }}}}, {{{{ backers }}}} neighbours raised <b>{{{{ raised }}}}</b>. We’re booking the crew now and will email you the date.</p>
{FUNDING_BOX}
{{{{ button(project_url, "See the project") }}}}"""),
    "project_scheduled": dict(
        group="Backers", label="Clean scheduled", when="You set a project to “Scheduled”.",
        vars=PERSON_VARS + PROJECT_VARS,
        subject="{{ project_title }}: clean booked{% if scheduled_for %} for {{ scheduled_for }}{% endif %}",
        html=f"""<h1 {H1}>The clean is booked{{% if scheduled_for %}} for {{{{ scheduled_for }}}}{{% endif %}}</h1>
<p {P}>Hi {{{{ name }}}}, our crew is scheduled to clean <b>{{{{ project_title }}}}</b>. If you can, please move cars off the verge on the day.</p>
{{{{ button(project_url, "See the plan") }}}}"""),
    "project_in_progress": dict(
        group="Backers", label="Crew on site", when="You set a project to “In progress”.",
        vars=PERSON_VARS + PROJECT_VARS,
        subject="The crew is on {{ project_title }} today",
        html=f"""<h1 {H1}>The crew is on site</h1>
<p {P}>Hi {{{{ name }}}}, work on <b>{{{{ project_title }}}}</b> has started. We’ll post photos on the project page as we go.</p>
{{{{ button(project_url, "Watch progress") }}}}"""),
    "project_completed": dict(
        group="Backers", label="Clean completed", when="You set a project to “Completed”.",
        vars=PERSON_VARS + PROJECT_VARS,
        subject="Done! {{ project_title }} has been cleaned",
        html=f"""<h1 {H1}>{{{{ project_title }}}} is clean</h1>
<p {P}>Hi {{{{ name }}}}, the job is done. Thank you for making it happen. The before-and-after photos are on the project page.</p>
{{{{ button(project_url, "See before and after") }}}}
<p {SMALL}>Want to keep it this way? Ask us about monthly upkeep for your street.</p>"""),
    "project_update": dict(
        group="Backers", label="Project update posted", when="You post an update and tick “Email backers and requester”.",
        vars=PERSON_VARS + PROJECT_VARS + UPDATE_VARS,
        subject="Update: {{ project_title }}",
        html=f"""<h1 {H1}>{{{{ update_title or ('New update on ' ~ project_title) }}}}</h1>
<p {P}>Hi {{{{ name }}}},</p>
{{% if update_body %}}<p style="margin:0 0 16px;white-space:pre-line">{{{{ update_body }}}}</p>{{% endif %}}
{{% if update_image_url %}}<p {P}><img src="{{{{ update_image_url }}}}" alt="" width="544" style="width:100%;max-width:544px;height:auto;border-radius:8px;display:block"></p>{{% endif %}}
{{% if update_video_url %}}<p {P}><a href="{{{{ update_video_url }}}}" style="color:{ROYAL};font-weight:700">Watch the video</a></p>{{% endif %}}
{{{{ button(project_url, "See the project") }}}}"""),
    "refund": dict(
        group="Backers", label="Refund issued", when="A contribution is refunded.",
        vars=PERSON_VARS + PROJECT_VARS + CONTRIBUTION_VARS + [("reason", "Why it was refunded")],
        subject="Your refund for {{ project_title }}",
        html=f"""<h1 {H1}>We’ve refunded {{{{ amount }}}}</h1>
<p {P}>Hi {{{{ name }}}}, your contribution to <b>{{{{ project_title }}}}</b> has been refunded to the card you paid with.</p>
{{% if reason %}}<div {BOX}><p style="margin:0">Reason: {{{{ reason }}}}</p></div>{{% endif %}}
<p {P}>Refunds usually show on your statement within 3 to 5 business days. Thank you for backing your street.</p>"""),
    # ---- team
    "admin_new_request": dict(
        group="Team", label="New request (team alert)", when="A new request arrives or a requester sends changes.",
        vars=REQUEST_VARS + [("admin_url", "Link to the request in admin"), ("description", "What needs doing")],
        subject="New cleaning request {{ ref }}: {{ street }}",
        html=f"""<h1 {H1}>Request {{{{ ref }}}}: {{{{ street }}}}</h1>
<p {P}>{{{{ full_name }}}} · {{{{ location }}}}</p>
<div {BOX}><p style="margin:0;white-space:pre-line">{{{{ description }}}}</p></div>
{{{{ button(admin_url, "Review in admin") }}}}"""),
    "admin_funded": dict(
        group="Team", label="Project funded (team alert)", when="A project reaches its goal.",
        vars=PROJECT_VARS + [("admin_url", "Link to the project in admin")],
        subject="Funded: {{ project_title }}",
        html=f"""<h1 {H1}>{{{{ project_title }}}} is funded</h1>
{FUNDING_BOX}
<p {P}>Book the crew and set the project to “Scheduled” so backers get the date.</p>
{{{{ button(admin_url, "Open in admin") }}}}"""),
}
GROUPS = ("Requester", "Backers", "Team")


# ---------------------------------------------------------------- rendering

_env = SandboxedEnvironment(autoescape=True, undefined=ChainableUndefined)
_env.globals.update(button=lambda url, label: Markup(_btn(url or "#", label)),
                    progress=lambda pct: Markup(_bar(pct)))


def override(key):
    try:
        return query("SELECT * FROM email_templates WHERE key=?", (key,), one=True)
    except Exception:  # noqa: BLE001 - table may not exist yet on a very old database
        return None


def source(key):
    """Return (subject, html, enabled, customised) for a template key."""
    if key == "layout":
        row = override("layout")
        return ("", row["html"] if row else LAYOUT, True, bool(row))
    d = TEMPLATES[key]
    row = override(key)
    if row:
        return (row["subject"] or d["subject"], row["html"] or d["html"], bool(row["enabled"]), True)
    return (d["subject"], d["html"], True, False)


def render_parts(subject_src, html_src, layout_src, ctx):
    """Render subject, full HTML and plain text. Raises TemplateError on bad syntax."""
    subject = " ".join(_env.from_string(subject_src).render(**ctx).split())[:150]
    subject = htmlmod.unescape(subject)
    body = _env.from_string(html_src).render(**ctx)
    preheader = re.sub(r"\s+", " ", html_to_text(body))[:140]
    full = _env.from_string(layout_src).render(**dict(ctx, content=Markup(body), subject=subject,
                                                       preheader=preheader))
    return subject, full, html_to_text(body)


def render(key, ctx):
    subject_src, html_src, _enabled, customised = source(key)
    layout_src = source("layout")[1]
    try:
        return render_parts(subject_src, html_src, layout_src, ctx)
    except TemplateError:
        if not customised and layout_src == LAYOUT:
            raise
        current_app.logger.exception("Edited email template %s failed; using the default", key)
        d = TEMPLATES[key]
        return render_parts(d["subject"], d["html"], LAYOUT, ctx)


def validate(subject_src, html_src, key):
    """Return an error message, or None if the template renders with sample data."""
    if len(html_src) > MAX_TEMPLATE_CHARS:
        return "That template is too long."
    for block in re.findall(r"\{[{%](.*?)[%}]\}", (subject_src or "") + html_src, re.S):
        if re.search(r"(?<!\w)_", re.sub(r"(['\"]).*?\1", "", block)):
            return "Template error: names starting with an underscore aren’t allowed in placeholders."
    try:
        if key == "layout":
            render_parts("Sample", "<p>Sample</p>", html_src, sample_context("request_received"))
            if "content" not in html_src:
                return "The layout must include {{ content }} where each email’s message goes."
        else:
            render_parts(subject_src, html_src, LAYOUT, sample_context(key))
    except TemplateError as exc:
        line = f" (line {exc.lineno})" if getattr(exc, "lineno", None) else ""
        return f"Template error{line}: {exc.message or exc}"
    except Exception as exc:  # noqa: BLE001 - sandbox SecurityError and similar
        return f"Template error: {exc}"
    return None


_TAG_A = re.compile(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)


def html_to_text(markup):
    text = re.sub(r"(?is)<(style|script|head|title)\b.*?</\1>", "", str(markup))
    text = re.sub(r'(?is)<div style="display:none[^>]*>.*?</div>', "", text)

    def link(m):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        href = htmlmod.unescape(m.group(1))
        return f"{label}: {href}" if label and label != href and not href.startswith("mailto:") else (label or href)

    text = _TAG_A.sub(link, text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|tr|table|li)>", "\n\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = htmlmod.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------- context builders


def abs_url(endpoint, **values):
    if has_request_context():
        return url_for(endpoint, _external=True, **values)
    base = current_app.config.get("SITE_URL") or "https://cleanmystreet.co.za"
    with current_app.test_request_context(base_url=base):
        return url_for(endpoint, _external=True, **values)


def base_context():
    s = get_settings()
    return {"site_name": "Clean My Street", "site_url": abs_url("public.home"),
            "contact_email": s.get("contact_email", ""), "contact_phone": s.get("contact_phone", ""),
            "year": datetime.now().year}


def first_name(full):
    return (str(full or "").strip().split() or ["there"])[0]


def request_context(req):
    req = dict(req)
    return {"name": first_name(req.get("requester_name")), "full_name": req.get("requester_name", ""),
            "ref": req.get("ref", ""), "street": req.get("street_name", ""),
            "location": ", ".join(x for x in (req.get("suburb"), req.get("city")) if x),
            "description": req.get("description", ""), "note": req.get("admin_note", ""),
            "track_url": abs_url("public.track", token=req["token"]) if req.get("token") else ""}


def project_context(p):
    return {"project_title": p["title"], "project_url": abs_url("public.project", slug=p["slug"]),
            "location": p.get("location", ""), "goal": rand(p["goal_cents"]), "raised": rand(p["raised_cents"]),
            "remaining": rand(p["remaining_cents"]), "pct": p["pct"], "backers": p["backers"],
            "deadline": nice_date(p["deadline"]), "days_left": max(0, p.get("days_left") or 0),
            "min_contribution": rand(p["min_contribution_cents"]),
            "service": "monthly" if p.get("service_type") == "monthly" else "once-off",
            "scheduled_for": nice_date(p.get("scheduled_for")) if p.get("scheduled_for") else "",
            "street": p.get("street_name", ""), "admin_url": abs_url("admin.project_detail", pid=p["id"])}


def sample_context(key):
    ctx = base_context()
    ctx.update({
        "name": "Thandi", "full_name": "Thandi Mokoena", "ref": "CMS-7K2Q", "street": "Beyers Naudé Drive",
        "location": "Northcliff, Johannesburg", "description": "Overgrown verges and blocked drains along the service lane.",
        "note": "Please add a photo of the drain at the corner of 4th Avenue.",
        "track_url": abs_url("public.home") + "track/sample", "project_title": "Beyers Naudé Drive service lane",
        "project_url": abs_url("public.projects"), "goal": "R18 500", "raised": "R12 300", "remaining": "R6 200",
        "pct": 66, "backers": 14, "deadline": "23 Oct 2026", "days_left": 3, "min_contribution": "R100",
        "service": "once-off", "scheduled_for": "28 Oct 2026", "amount": "R250", "contribution_id": 1042,
        "reason": "Funding goal not reached by the deadline",
        "update_title": "Halfway there on the verges",
        "update_body": "The crew cleared the first 300 m this morning and unblocked two drains.",
        "update_image_url": abs_url("static", filename="img/gallery/aerial-crew.jpg"), "update_video_url": "",
        "admin_url": abs_url("admin.dashboard"),
    })
    return ctx


# ---------------------------------------------------------------- outbox log


def log(to, key, subject, status, error="", ref_type="", ref_id=None):
    try:
        execute("INSERT INTO email_log(to_addr, template, subject, status, error, ref_type, ref_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (str(to)[:200], key, subject[:200], status, (error or "")[:300],
                                            ref_type, ref_id, now_iso()))
    except Exception:  # noqa: BLE001 - logging must never break sending
        current_app.logger.exception("Could not write email log")


def history(ref_type, ref_id, limit=30):
    try:
        return query("SELECT * FROM email_log WHERE ref_type=? AND ref_id=? ORDER BY id DESC LIMIT ?",
                     (ref_type, ref_id, limit))
    except Exception:  # noqa: BLE001
        return []
