"""Email notifications.

If SMTP_HOST is configured, messages are sent through it (any provider works:
Google Workspace, Microsoft 365, Mailgun, Amazon SES SMTP, Afrihost, etc.).
Otherwise they are written to the application log, so nothing breaks in
development.
"""
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app, render_template


def send(to, subject, template, **ctx):
    if not to:
        return False
    body = render_template(f"email/{template}.txt", **ctx)
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        current_app.logger.info("EMAIL (not sent, SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    if cfg.get("MAIL_REPLY_TO"):
        msg["Reply-To"] = cfg["MAIL_REPLY_TO"]
    msg.set_content(body)
    try:
        port = int(cfg.get("SMTP_PORT", 587))
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, context=ssl.create_default_context(), timeout=15)
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=15)
            server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.get("SMTP_USER"):
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 - never let email failure break a request
        current_app.logger.exception("Failed to send email to %s", to)
        return False


def notify_admins(subject, template, **ctx):
    to = current_app.config.get("ADMIN_NOTIFY_EMAIL")
    if to:
        send(to, subject, template, **ctx)
