"""Yoco Checkout API integration.

Flow
----
1. A contributor submits the form on a project page. We store a `pending`
   contribution and create a Yoco checkout (POST /api/checkouts) with the
   contribution id in metadata.
2. The contributor pays on Yoco's hosted page and is redirected back to
   /pay/return/<token>. We confirm the status by reading the checkout from
   Yoco (never trusting the redirect alone).
3. Yoco also POSTs a signed `payment.succeeded` webhook to /webhooks/yoco.
   Whichever arrives first marks the contribution paid; both are idempotent.
4. Refunds (goal missed, project cancelled, or manual) call
   POST /api/checkouts/{id}/refund. A `refund.succeeded` webhook confirms them.

Modes (PAYMENT_MODE)
--------------------
- "yoco": real Yoco calls. Use an sk_test_ key for sandbox, sk_live_ for production.
- "demo": no money moves. The checkout is a local page with "simulate" buttons.
  Demo mode is refused when FLASK_ENV/APP_ENV is production.

Webhook signatures follow the Standard Webhooks scheme Yoco uses:
headers webhook-id, webhook-timestamp, webhook-signature ("v1,<base64>"),
signed content "<id>.<timestamp>.<raw body>", HMAC-SHA256 keyed with the
base64-decoded part of the secret after "whsec_".
"""
import base64
import hashlib
import hmac
import time

import requests
from flask import current_app

API_BASE = "https://payments.yoco.com/api"
SIGNATURE_TOLERANCE_SECONDS = 180


class PaymentError(Exception):
    pass


# ---------------------------------------------------------------- settings
# Keys can come from environment variables (PAYMENT_MODE, YOCO_SECRET_KEY, YOCO_WEBHOOK_SECRET) or be
# saved by an admin in Site settings. Saved keys win, and are stored encrypted with SECRET_KEY.

SECRET_FIELDS = ("yoco_secret_key", "yoco_webhook_secret")


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - listed in requirements.txt
        return None
    digest = hashlib.sha256(("cms-payments:" + current_app.config["SECRET_KEY"]).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    f = _fernet()
    if not f:
        raise PaymentError("The server is missing the 'cryptography' package, so keys can't be stored safely.")
    return "enc1:" + f.encrypt(value.encode()).decode()


def decrypt(stored):
    if not stored:
        return ""
    if not stored.startswith("enc1:"):
        return ""
    f = _fernet()
    if not f:
        return ""
    try:
        return f.decrypt(stored[5:].encode()).decode()
    except Exception:  # noqa: BLE001 - SECRET_KEY changed, or tampered value
        return ""


def config():
    """Effective payment settings for this request: {mode, secret_key, webhook_secret, source, ...}."""
    from flask import g
    if "payment_config" in g:
        return g.payment_config
    cfg = current_app.config
    saved = {}
    try:
        from .db import get_settings
        s = get_settings()
        saved = {"mode": s.get("payment_mode_override", ""),
                 "secret_key": decrypt(s.get("yoco_secret_key", "")),
                 "webhook_secret": decrypt(s.get("yoco_webhook_secret", "")),
                 "unreadable": bool(s.get("yoco_secret_key")) and not decrypt(s.get("yoco_secret_key", ""))}
    except Exception:  # noqa: BLE001 - before the database exists
        pass
    secret = saved.get("secret_key") or cfg.get("YOCO_SECRET_KEY", "")
    webhook = saved.get("webhook_secret") or cfg.get("YOCO_WEBHOOK_SECRET", "")
    chosen = saved.get("mode") if saved.get("mode") in ("demo", "yoco") else cfg.get("PAYMENT_MODE", "demo")
    result = {
        "mode": chosen,
        "secret_key": secret,
        "webhook_secret": webhook,
        "key_source": "admin" if saved.get("secret_key") else ("env" if cfg.get("YOCO_SECRET_KEY") else ""),
        "webhook_source": "admin" if saved.get("webhook_secret") else ("env" if cfg.get("YOCO_WEBHOOK_SECRET") else ""),
        "mode_source": "admin" if saved.get("mode") in ("demo", "yoco") else "env",
        "live": secret.startswith("sk_live_"),
        "unreadable": saved.get("unreadable", False),
    }
    g.payment_config = result
    return result


def forget_config():
    from flask import g
    g.pop("payment_config", None)


def mask(value):
    if not value:
        return ""
    head = value[:8] if value.startswith(("sk_", "whsec_")) else value[:3]
    return f"{head}…{value[-4:]}"


def mode():
    return config()["mode"]


def _headers(idempotency_key=None):
    key = config()["secret_key"]
    if not key:
        raise PaymentError("No Yoco secret key is set. Add it in Admin → Site settings → Payments.")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _call(method, path, payload=None, idempotency_key=None):
    try:
        resp = requests.request(
            method,
            f"{API_BASE}{path}",
            json=payload,
            headers=_headers(idempotency_key),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach Yoco: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        current_app.logger.error("Yoco %s %s failed: %s %s", method, path, resp.status_code, detail)
        raise PaymentError(f"Yoco returned {resp.status_code}: {detail}")
    return resp.json() if resp.content else {}


def create_checkout(contribution, project, base_url):
    """Create a hosted checkout. Returns (checkout_id, redirect_url)."""
    token = contribution["return_token"]
    if mode() == "demo":
        checkout_id = f"demo_{token[:16]}"
        return checkout_id, f"{base_url}/pay/demo/{token}"

    data = _call(
        "POST",
        "/checkouts",
        {
            "amount": int(contribution["amount_cents"]),
            "currency": "ZAR",
            "successUrl": f"{base_url}/pay/return/{token}",
            "cancelUrl": f"{base_url}/pay/cancel/{token}",
            "failureUrl": f"{base_url}/pay/return/{token}?failed=1",
            "metadata": {
                "contributionId": str(contribution["id"]),
                "projectId": str(project["id"]),
                "projectSlug": project["slug"],
            },
            "externalId": f"contribution-{contribution['id']}",
        },
        idempotency_key=f"checkout-{contribution['id']}-{token[:8]}",
    )
    return data["id"], data["redirectUrl"]


def get_checkout(checkout_id):
    if mode() == "demo":
        return None
    return _call("GET", f"/checkouts/{checkout_id}")


def checkout_is_paid(checkout):
    """Yoco reports a paid checkout with status 'completed' and a paymentId."""
    if not checkout:
        return False
    return checkout.get("status") in ("completed", "succeeded") or bool(checkout.get("paymentId"))


def refund_checkout(checkout_id, amount_cents=None, reason=""):
    """Refund a paid checkout in full (or partially). Returns (status, refund_id)."""
    if mode() == "demo" or str(checkout_id).startswith("demo_"):
        return "succeeded", f"demo_refund_{int(time.time())}"
    payload = {"metadata": {"reason": reason[:120]}}
    if amount_cents:
        payload["amount"] = int(amount_cents)
    data = _call("POST", f"/checkouts/{checkout_id}/refund", payload,
                 idempotency_key=f"refund-{checkout_id}")
    return data.get("status", "pending"), data.get("refundId")


def register_webhook(url, name="clean-my-street"):
    """Register our webhook URL with Yoco. The response includes the signing secret."""
    return _call("POST", "/webhooks", {"name": name, "url": url})


def check_keys():
    """Ask Yoco whether the secret key works, without moving money. Returns (ok, message)."""
    try:
        resp = requests.get(f"{API_BASE}/webhooks", headers=_headers(), timeout=15)
    except PaymentError as exc:
        return False, str(exc)
    except requests.RequestException as exc:
        return False, f"Couldn’t reach Yoco: {exc}"
    if resp.status_code in (401, 403):
        return False, "Yoco rejected the secret key. Copy it again from the Yoco portal (Sales → Payment gateway)."
    if resp.status_code >= 400:
        return False, f"Yoco answered with an error ({resp.status_code}). The key may still be fine; try a small payment."
    kind = "live" if config()["live"] else "test"
    return True, f"Yoco accepted the {kind} secret key."


def verify_webhook(headers, raw_body):
    """Return True when the delivery is authentic and fresh."""
    secret = config()["webhook_secret"]
    if not secret:
        current_app.logger.error("Yoco webhook secret not set; rejecting webhook.")
        return False
    msg_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signatures = headers.get("webhook-signature", "")
    if not (msg_id and timestamp and signatures):
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False
    key_part = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(key_part)
    except ValueError:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for entry in signatures.split():
        version, _, sig = entry.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False
