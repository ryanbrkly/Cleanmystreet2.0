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


def mode():
    return current_app.config["PAYMENT_MODE"]


def _headers(idempotency_key=None):
    key = current_app.config["YOCO_SECRET_KEY"]
    if not key:
        raise PaymentError("YOCO_SECRET_KEY is not configured.")
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


def verify_webhook(headers, raw_body):
    """Return True when the delivery is authentic and fresh."""
    secret = current_app.config["YOCO_WEBHOOK_SECRET"]
    if not secret:
        current_app.logger.error("YOCO_WEBHOOK_SECRET not set; rejecting webhook.")
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
