"""
Thin wrapper around the IntaSend Python SDK (M-Pesa STK push collections).
Credentials come from Flask's app.config, which config.py populates from
environment variables (see .env.example) — nothing sensitive lives in
source control.

If credentials aren't configured, is_configured() returns False and the
subscription page falls back to a clearly-labelled local simulation mode,
so the trial -> payment -> webhook -> reactivation flow can still be
tested end-to-end without live IntaSend credentials. Once real keys are
set, the simulation option disappears automatically — no code changes
needed.
"""
from flask import current_app

try:
    from intasend import APIService
except ImportError:
    APIService = None


def is_configured():
    return bool(current_app.config.get("INTASEND_SECRET_KEY")) and APIService is not None


def get_service():
    if not is_configured():
        return None
    return APIService(
        token=current_app.config["INTASEND_SECRET_KEY"],
        publishable_key=current_app.config.get("INTASEND_PUBLISHABLE_KEY", ""),
        test=current_app.config.get("INTASEND_TEST_MODE", True),
    )


def initiate_stk_push(phone_number, email, amount, narrative, api_ref):
    """Kicks off an M-Pesa STK push prompt on the payer's phone.
    Returns (success, invoice_id_or_None, error_or_None)."""
    service = get_service()
    if not service:
        return False, None, "Payments aren't configured yet — contact the platform administrator."
    try:
        response = service.collect.mpesa_stk_push(
            phone_number=phone_number, email=email, amount=amount,
            narrative=narrative, api_ref=api_ref,
        )
        invoice = response.get("invoice", {}) if isinstance(response, dict) else {}
        invoice_id = invoice.get("invoice_id") or invoice.get("id")
        return True, invoice_id, None
    except Exception as exc:
        return False, None, str(exc)


def check_status(invoice_id):
    """Returns the IntaSend invoice state string (e.g. COMPLETE, FAILED,
    PENDING, PROCESSING), or None if it can't be checked right now."""
    service = get_service()
    if not service:
        return None
    try:
        response = service.collect.status(invoice_id=invoice_id)
        invoice = response.get("invoice", {}) if isinstance(response, dict) else {}
        return invoice.get("state")
    except Exception:
        return None


def verify_webhook_challenge(payload_challenge):
    expected = current_app.config.get("INTASEND_WEBHOOK_CHALLENGE")
    if not expected:
        return False
    return payload_challenge == expected
