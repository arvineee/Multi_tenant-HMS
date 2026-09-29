"""Enforcement helpers. Any code path that sends a patient's data to a third
party (SHA claims, the national HIE, ...) must go through one of these first."""
from functools import wraps

from flask import jsonify
from flask_login import current_user

from app.audit.service import log_denied
from app.consent import service
from app.consent.models import CONSENT_PURPOSES

# Insurance scheme types whose claims are sent to SHA and so need sha_claims consent.
SHA_SCHEME_TYPES = {"NHIF/SHA"}


def consent_block_reason(patient_id, purpose):
    """None if sharing is allowed, otherwise a message for the user."""
    if service.has_consent(patient_id, purpose):
        return None
    return (f"Patient consent is missing for: {CONSENT_PURPOSES.get(purpose, purpose)}. "
            "Record it on the patient's page first.")


def check_consent_or_deny(user, patient_id, purpose, action):
    """Returns None if allowed. Otherwise logs a denied audit entry and
    returns the message to show."""
    reason = consent_block_reason(patient_id, purpose)
    if reason:
        log_denied(user, action, patient_id, f"no '{purpose}' consent")
    return reason


def bill_needs_sha_consent(bill):
    s = bill.insurance_scheme
    return bool(s and s.scheme_type in SHA_SCHEME_TYPES)


def consent_required(purpose, patient_arg="patient_id"):
    """Route decorator for URLs carrying a patient id. Place it below
    @login_required. Returns 403 JSON and audits the refusal."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            reason = check_consent_or_deny(
                current_user, kwargs.get(patient_arg), purpose, f"share_{purpose}")
            if reason:
                return jsonify(success=False, error=reason), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator
