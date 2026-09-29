"""MFA enrolment and verification (DB layer over totp.py)."""
import json

from flask import current_app

from app.extensions import db
from app.models import now, log_action
from app.security import crypto, totp
from app.security.models import UserMfa, OrgSecurityPolicy


def _pepper():
    return crypto.derive_key(current_app.config["SECRET_KEY"], info=b"mfa-backup").hex()


def get_mfa(user):
    return UserMfa.query.filter_by(user_id=user.id).first()


def is_enabled(user):
    m = get_mfa(user)
    return bool(m and m.is_enabled)


def required_for(user):
    """MFA is mandatory for privileged roles, or for everyone if the org chose 'all'."""
    if user.role and user.role.name in current_app.config.get("MFA_REQUIRED_ROLES", []):
        return True
    pol = OrgSecurityPolicy.query.filter_by(organization_id=user.organization_id).first()
    return bool(pol and pol.mfa_policy == "all")


def must_enrol(user):
    return required_for(user) and not is_enabled(user)


def begin_enrolment(user):
    m = get_mfa(user)
    if m and m.is_enabled:
        raise ValueError("MFA is already enabled.")
    secret = totp.generate_secret()
    if not m:
        m = UserMfa(user_id=user.id, secret=secret)
        db.session.add(m)
    else:
        m.secret = secret
    log_action(user, "mfa_enrolment_started", "User", user.id)
    db.session.commit()
    return secret, totp.provisioning_uri(secret, user.username, current_app.config["MFA_ISSUER"])


def confirm_enrolment(user, code):
    m = get_mfa(user)
    if not m or m.is_enabled:
        raise ValueError("Start enrolment first.")
    step = totp.verify(m.secret, code)
    if step is None:
        return None
    codes = totp.generate_backup_codes(8)
    m.backup_code_hashes = json.dumps([totp.hash_backup_code(c, _pepper()) for c in codes])
    m.is_enabled, m.last_used_step, m.enrolled_at = True, step, now()
    log_action(user, "mfa_enabled", "User", user.id)
    db.session.commit()
    return codes


def verify_login(user, code):
    """TOTP (replay-protected) or a single-use backup code. Returns True/False."""
    m = get_mfa(user)
    if not m or not m.is_enabled:
        return False
    step = totp.verify(m.secret, code, last_used_step=m.last_used_step)
    if step is not None:
        m.last_used_step = step
        db.session.commit()
        return True
    rest = totp.consume_backup_code(code, json.loads(m.backup_code_hashes or "[]"), _pepper())
    if rest is not None:
        m.backup_code_hashes = json.dumps(rest)
        log_action(user, "mfa_backup_code_used", "User", user.id, {"remaining": len(rest)})
        db.session.commit()
        return True
    return False


def backup_codes_left(user):
    m = get_mfa(user)
    return len(json.loads(m.backup_code_hashes or "[]")) if m and m.is_enabled else 0


def disable(user, code, actor=None):
    """User disables their own MFA (needs a valid code) unless policy requires it."""
    if required_for(user) and actor is None:
        raise ValueError("MFA is required for your role and can't be turned off.")
    if actor is None and not verify_login(user, code):
        raise ValueError("That code is not valid.")
    m = get_mfa(user)
    if m:
        db.session.delete(m)
    log_action(actor or user, "mfa_disabled", "User", user.id)
    db.session.commit()


def reset_for(user, actor):
    """Admin reset for a locked-out user; they must re-enrol."""
    m = get_mfa(user)
    if m:
        db.session.delete(m)
    log_action(actor, "mfa_reset", "User", user.id)
    db.session.commit()
