"""Sending email.

Messages are built from the editable templates in cms/emails.py and sent as
HTML with a plain-text alternative. If SMTP_HOST is configured they go through
it (any provider works: Google Workspace, Microsoft 365, Mailgun, Amazon SES
SMTP, Afrihost, etc.). Otherwise they are written to the application log, so
nothing breaks in development. Every attempt is recorded in the email log that
admins can see.
"""
import smtplib
import ssl
from contextlib import contextmanager
from email.message import EmailMessage

from flask import current_app

from . import emails
from .db import get_settings
from .utils import valid_email


def _tls_context():
    ctx = ssl.create_default_context()
    if not current_app.config.get("SMTP_TLS_VERIFY", True):
        # Still encrypted, but the server's certificate isn't checked. Only for mail hosts with a
        # broken certificate, and only until the host fixes it.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def explain(exc):
    """Turn a mail-server error into a sentence an admin can act on."""
    text = str(exc)
    if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        if "expired" in text:
            why = "the mail server’s security certificate has expired"
        elif "hostname" in text or "match" in text:
            why = "the mail server’s certificate doesn’t match SMTP_HOST"
        else:
            why = "the mail server’s security certificate couldn’t be verified"
        return (f"Not sent: {why}. Ask your email host to renew it, or set SMTP_HOST to the server name on their "
                f"certificate (often your hosting server’s own name). Short-term workaround: set "
                f"SMTP_TLS_VERIFY=false in Vercel and redeploy.")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Not sent: the mail server rejected SMTP_USER / SMTP_PASSWORD. Check them in Vercel."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "Not sent: the mail server won’t send from MAIL_FROM. Use an address on the account in SMTP_USER."
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return f"Not sent: couldn’t reach {current_app.config.get('SMTP_HOST')} on port {current_app.config.get('SMTP_PORT')}. ({text})"
    return f"Not sent: {text}"


@contextmanager
def _smtp():
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        yield None
        return
    port = int(cfg.get("SMTP_PORT", 587))
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, context=_tls_context(), timeout=15)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=15)
        server.starttls(context=_tls_context())
    try:
        if cfg.get("SMTP_USER"):
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        yield server
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def _deliver(server, to, subject, html, text):
    cfg = current_app.config
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = " ".join(str(to).split())[:200]
    msg["Subject"] = " ".join(str(subject).split())[:150]  # no CR/LF: blocks header injection
    reply_to = cfg.get("MAIL_REPLY_TO") or get_settings().get("contact_email")
    if reply_to and valid_email(reply_to):
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    server.send_message(msg)


def send_many(key, recipients, ref_type="", ref_id=None, force=False):
    """Send one template to several people. recipients: list of (email, context dict).

    Returns the number actually sent. Never raises: email problems must not break
    payments, approvals or webhooks.
    """
    sent = 0
    recipients = [(to, ctx) for to, ctx in recipients if to and valid_email(to)]
    if not recipients:
        return 0
    try:
        enabled = emails.source(key)[2] if key in emails.TEMPLATES else True
        if not enabled and not force:
            for to, _ctx in recipients:
                emails.log(to, key, emails.TEMPLATES[key]["label"], "off", "Email turned off in Admin → Emails",
                           ref_type, ref_id)
            return 0
        base = emails.base_context()
        rendered = []
        for to, ctx in recipients:
            subject, html, text = emails.render(key, dict(base, **ctx))
            rendered.append((to, subject, html, text))
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Could not build email %s", key)
        for to, _ctx in recipients:
            emails.log(to, key, key, "failed", f"Template problem: {exc}", ref_type, ref_id)
        return 0
    try:
        with _smtp() as server:
            for to, subject, html, text in rendered:
                if server is None:
                    current_app.logger.info("EMAIL (not sent, SMTP not configured) to=%s subject=%s\n%s",
                                            to, subject, text)
                    emails.log(to, key, subject, "logged", "SMTP not configured", ref_type, ref_id)
                    continue
                try:
                    _deliver(server, to, subject, html, text)
                    emails.log(to, key, subject, "sent", "", ref_type, ref_id)
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    current_app.logger.exception("Failed to send email to %s", to)
                    emails.log(to, key, subject, "failed", explain(exc), ref_type, ref_id)
    except Exception as exc:  # noqa: BLE001 - could not connect / log in
        current_app.logger.exception("SMTP connection failed")
        for to, subject, _h, _t in rendered[sent:]:
            emails.log(to, key, subject, "failed", explain(exc), ref_type, ref_id)
    return sent


def check_connection():
    """Connect and sign in to the mail server without sending. Returns (ok, message)."""
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        return False, "Email isn’t set up yet: add SMTP_HOST and the other SMTP settings in Vercel, then redeploy."
    try:
        with _smtp():
            pass
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    note = "" if cfg.get("SMTP_TLS_VERIFY", True) else " (certificate checking is turned off)"
    return True, f"Connected to {cfg['SMTP_HOST']} and signed in{note}. Emails can be sent."


def deliver_now(to, key, subject, html, text):
    """Send an already-rendered message (used for admin test emails). Returns (sent, message)."""
    try:
        with _smtp() as server:
            if server is None:
                emails.log(to, key, subject, "logged", "SMTP not configured", "test", None)
                return False, ("Not sent: email isn’t set up on the server yet. Add the SMTP settings in Vercel "
                               "and redeploy.")
            _deliver(server, to, subject, html, text)
        emails.log(to, key, subject, "sent", "", "test", None)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Test email failed")
        msg = explain(exc)
        emails.log(to, key, subject, "failed", msg, "test", None)
        return False, msg


def send(to, key, ref_type="", ref_id=None, force=False, **ctx):
    return send_many(key, [(to, ctx)], ref_type, ref_id, force) > 0


def admin_recipients():
    raw = get_settings().get("admin_notify_email") or current_app.config.get("ADMIN_NOTIFY_EMAIL") or ""
    return [a.strip() for a in raw.replace(";", ",").split(",") if valid_email(a.strip())]


def notify_admins(key, ref_type="", ref_id=None, **ctx):
    return send_many(key, [(a, ctx) for a in admin_recipients()], ref_type, ref_id)
