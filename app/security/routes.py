import datetime
import io
import time

from flask import (Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app, abort, Response)
from flask_login import login_required, login_user, current_user

from app.extensions import db, csrf
from app.decorators import permission_required
from app.models import User, Patient, Organization, Hospital, log_action, now
from app.audit.service import log_denied
from app.security import mfa_service, oidc, integrity
from app.security.models import (OrgSecurityPolicy, EmergencyAccessGrant, DataProtectionRegistration, DpiaRecord,
                                 ExternalIdentity)

security_bp = Blueprint("security", __name__, template_folder="../templates/security")
MFA_PENDING_SECONDS = 300


def _data():
    return request.get_json(silent=True) or request.form


def _date(v):
    return datetime.date.fromisoformat(v) if v else None


# ---------------------------------------------------------------------------
# MFA: login step, enrolment, management
# ---------------------------------------------------------------------------
@security_bp.route("/security/mfa/verify", methods=["GET", "POST"])
@csrf.exempt  # runs before the login completes, like the login form itself
def mfa_verify():
    uid = session.get("mfa_pending_user_id")
    started = session.get("mfa_pending_at", 0)
    user = db.session.get(User, uid) if uid else None
    if not user or time.time() - started > MFA_PENDING_SECONDS:
        session.pop("mfa_pending_user_id", None)
        return redirect(url_for("auth.login"))
    if request.method == "GET":
        return render_template("security/mfa_verify.html")
    if user.is_locked_out:
        return jsonify(success=False, error="Too many failed attempts. Try again later."), 429
    if mfa_service.verify_login(user, _data().get("code")):
        session.pop("mfa_pending_user_id", None)
        session.pop("mfa_pending_at", None)
        user.register_successful_login()
        login_user(user)
        session.permanent = True
        log_action(user, "login", ip_address=request.remote_addr, details={"mfa": True})
        db.session.commit()
        return jsonify(success=True, redirect=url_for("auth.change_password" if user.must_change_password else "main.dashboard"))
    locked = user.register_failed_login(current_app.config["LOGIN_MAX_ATTEMPTS"], current_app.config["LOGIN_LOCKOUT_MINUTES"])
    log_action(user, "mfa_failed", ip_address=request.remote_addr)
    db.session.commit()
    if locked:
        session.pop("mfa_pending_user_id", None)
        return jsonify(success=False, error="Too many failed attempts. Account locked."), 429
    return jsonify(success=False, error="That code is not valid."), 401


@security_bp.route("/security/mfa")
@login_required
def mfa_setup():
    enabled = mfa_service.is_enabled(current_user)
    return render_template("security/mfa_setup.html", enabled=enabled, required=mfa_service.required_for(current_user),
                           codes_left=mfa_service.backup_codes_left(current_user))


@security_bp.route("/security/mfa/start", methods=["POST"])
@login_required
def mfa_start():
    try:
        secret, uri = mfa_service.begin_enrolment(current_user)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    return jsonify(success=True, secret=secret, uri=uri)


@security_bp.route("/security/mfa/qr.svg")
@login_required
def mfa_qr():
    """QR image drawn in the browser is not possible without a JS lib, so we
    render it server-side only if the optional `segno` package is installed."""
    m = mfa_service.get_mfa(current_user)
    if not m or m.is_enabled:
        abort(404)
    try:
        import segno
    except ImportError:
        abort(404)
    from app.security import totp
    buf = io.BytesIO()
    segno.make(totp.provisioning_uri(m.secret, current_user.username, current_app.config["MFA_ISSUER"])).save(buf, kind="svg", scale=5)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@security_bp.route("/security/mfa/confirm", methods=["POST"])
@login_required
def mfa_confirm():
    try:
        codes = mfa_service.confirm_enrolment(current_user, _data().get("code"))
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    if codes is None:
        return jsonify(success=False, error="That code is not valid. Check your phone's clock and try again."), 400
    return jsonify(success=True, backup_codes=codes)


@security_bp.route("/security/mfa/disable", methods=["POST"])
@login_required
def mfa_disable():
    try:
        mfa_service.disable(current_user, _data().get("code"))
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    return jsonify(success=True)


@security_bp.route("/security/users/<int:user_id>/mfa-reset", methods=["POST"])
@login_required
@permission_required("security.manage")
def mfa_reset(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.organization_id != current_user.organization_id or u.id == current_user.id:
        abort(403)
    mfa_service.reset_for(u, current_user)
    return jsonify(success=True)


@security_bp.route("/security/policy", methods=["GET", "POST"])
@login_required
@permission_required("security.manage")
def policy():
    pol = OrgSecurityPolicy.query.filter_by(organization_id=current_user.organization_id).first()
    if request.method == "POST":
        val = _data().get("mfa_policy")
        if val not in ("privileged", "all"):
            return jsonify(success=False, error="Invalid policy."), 400
        if not pol:
            pol = OrgSecurityPolicy(organization_id=current_user.organization_id)
            db.session.add(pol)
        pol.mfa_policy, pol.updated_by_id = val, current_user.id
        log_action(current_user, "update", "OrgSecurityPolicy", None, {"mfa_policy": val})
        db.session.commit()
        return jsonify(success=True)
    users = User.query.filter_by(organization_id=current_user.organization_id, is_active=True).order_by(User.full_name).all()
    return render_template("security/policy.html", policy=pol.mfa_policy if pol else "privileged", users=users,
                           enrolled={u.id for u in users if mfa_service.is_enabled(u)},
                           privileged_roles=current_app.config["MFA_REQUIRED_ROLES"])


# ---------------------------------------------------------------------------
# Emergency ("break-glass") access
# ---------------------------------------------------------------------------
@security_bp.route("/security/emergency-access", methods=["GET"])
@login_required
@permission_required("emergency.access")
def emergency_form():
    mine = (EmergencyAccessGrant.query.filter_by(user_id=current_user.id)
            .order_by(EmergencyAccessGrant.granted_at.desc()).limit(10).all())
    return render_template("security/emergency_form.html", mine=mine, minutes=current_app.config["EMERGENCY_ACCESS_MINUTES"])


@security_bp.route("/security/emergency-access", methods=["POST"])
@login_required
@permission_required("emergency.access")
def emergency_request():
    d = _data()
    if d.get("patient_id"):
        patient = db.session.get(Patient, int(d["patient_id"])) or abort(404)
    else:
        num = (d.get("patient_number") or "").strip()
        patient = (Patient.query.join(Patient.hospital).filter(Patient.patient_number == num,
                   Hospital.organization_id == current_user.organization_id).first()) if num else None
        if not patient:
            return jsonify(success=False, error="No patient with that number in your organization."), 404
    if patient.hospital.organization_id != current_user.organization_id:
        log_denied(current_user, "emergency_access", patient.id, "different organization")
        abort(403)
    reason = (d.get("reason") or "").strip()
    if len(reason) < 15:
        return jsonify(success=False, error="Describe the emergency (at least a sentence)."), 400
    g = EmergencyAccessGrant(user_id=current_user.id, patient_id=patient.id, hospital_id=patient.hospital_id, reason=reason[:500],
                             expires_at=now() + datetime.timedelta(minutes=current_app.config["EMERGENCY_ACCESS_MINUTES"]))
    db.session.add(g)
    db.session.flush()
    log_action(current_user, "emergency_access_granted", "EmergencyAccessGrant", g.id, {"reason": reason[:200]},
               patient_id=patient.id)
    db.session.commit()
    return jsonify(success=True, redirect=url_for("records.record_page", patient_id=patient.id),
                   expires_at=g.expires_at.isoformat())


@security_bp.route("/security/emergency-access/review", methods=["GET", "POST"])
@login_required
@permission_required("security.manage")
def emergency_review():
    org_id = current_user.organization_id
    if request.method == "POST":
        d = _data()
        g = db.session.get(EmergencyAccessGrant, int(d.get("id") or 0)) or abort(404)
        if g.user.organization_id != org_id:
            abort(403)
        if d.get("outcome") not in ("Justified", "Not justified"):
            return jsonify(success=False, error="Choose an outcome."), 400
        g.review_outcome, g.reviewed_by_id, g.reviewed_at = d["outcome"], current_user.id, now()
        g.review_notes = (d.get("notes") or "")[:500] or None
        if g.expires_at > now():
            g.revoked_at = now()
        log_action(current_user, "emergency_access_reviewed", "EmergencyAccessGrant", g.id, {"outcome": d["outcome"]}, patient_id=g.patient_id)
        db.session.commit()
        return jsonify(success=True)
    grants = (EmergencyAccessGrant.query.join(User, User.id == EmergencyAccessGrant.user_id)
              .filter(User.organization_id == org_id).order_by(EmergencyAccessGrant.granted_at.desc()).limit(200).all())
    return render_template("security/emergency_review.html", grants=grants)


# ---------------------------------------------------------------------------
# Data protection register (ODPC + DPIA)
# ---------------------------------------------------------------------------
@security_bp.route("/security/data-protection", methods=["GET", "POST"])
@login_required
@permission_required("security.manage")
def data_protection():
    org_id = current_user.organization_id
    platform = current_user.role.scope == "platform"
    if request.method == "POST":
        d = _data()
        try:
            kind = d.get("kind")
            if kind == "registration":
                role = d.get("role")
                if role not in ("controller", "processor"):
                    return jsonify(success=False, error="Choose controller or processor."), 400
                if role == "processor" and not platform:
                    return jsonify(success=False, error="The processor registration is recorded by the platform operator."), 403
                if not (d.get("registered_name") or "").strip() or not (d.get("registration_number") or "").strip():
                    return jsonify(success=False, error="Name and registration number are required."), 400
                target_org = None if role == "processor" else org_id
                row = DataProtectionRegistration.query.filter_by(organization_id=target_org, role=role).first() or DataProtectionRegistration(
                    organization_id=target_org, role=role)
                row.registered_name, row.registration_number = d["registered_name"].strip()[:200], d["registration_number"].strip()[:60]
                row.issued_on, row.expires_on = _date(d.get("issued_on")), _date(d.get("expires_on"))
                row.evidence_reference, row.recorded_by_id = (d.get("evidence_reference") or "")[:300] or None, current_user.id
                db.session.add(row)
            elif kind == "dpia":
                if not (d.get("title") or "").strip() or not (d.get("assessor_name") or "").strip() or not d.get("completed_on"):
                    return jsonify(success=False, error="Title, assessor and completion date are required."), 400
                db.session.add(DpiaRecord(organization_id=None if (platform and d.get("platform_level")) else org_id,
                                          title=d["title"].strip()[:200], version=(d.get("version") or "1.0")[:20],
                                          scope=(d.get("scope") or "")[:500] or None, risk_level=d.get("risk_level") if d.get("risk_level") in ("Low", "Medium", "High") else "Medium",
                                          mitigations_summary=d.get("mitigations_summary"), assessor_name=d["assessor_name"].strip()[:150],
                                          completed_on=_date(d["completed_on"]), next_review_on=_date(d.get("next_review_on")),
                                          evidence_reference=(d.get("evidence_reference") or "")[:300] or None, recorded_by_id=current_user.id))
            else:
                return jsonify(success=False, error="Unknown form."), 400
        except ValueError:
            return jsonify(success=False, error="Dates must be YYYY-MM-DD."), 400
        log_action(current_user, "update", "DataProtection", None, {"kind": kind})
        db.session.commit()
        return jsonify(success=True)
    regs = DataProtectionRegistration.query.filter(db.or_(DataProtectionRegistration.organization_id == org_id,
                                                          DataProtectionRegistration.organization_id.is_(None))).all()
    dpias = DpiaRecord.query.filter(db.or_(DpiaRecord.organization_id == org_id, DpiaRecord.organization_id.is_(None))).order_by(
        DpiaRecord.completed_on.desc()).all()
    return render_template("security/data_protection.html", regs=regs, dpias=dpias, platform=platform)


# ---------------------------------------------------------------------------
# Audit trail integrity
# ---------------------------------------------------------------------------
@security_bp.route("/security/audit-integrity")
@login_required
@permission_required("audit.view")
def audit_integrity():
    from app.compliance.service import _audit_check
    return render_template("security/audit_integrity.html", result=_audit_check(),
                           retention=current_app.config["AUDIT_RETENTION_YEARS"])


# ---------------------------------------------------------------------------
# Digital Health ID sign-in
# ---------------------------------------------------------------------------
def _http():
    import requests
    return requests


@security_bp.route("/security/dha/login")
@csrf.exempt
def dha_login():
    cfg = current_app.config
    if not oidc.is_configured(cfg):
        return render_template("security/dha_unavailable.html"), 503
    intent = "link" if request.args.get("link") and current_user.is_authenticated else "login"
    try:
        doc = oidc.discover(cfg, _http())
    except oidc.OidcError as e:
        return render_template("security/dha_unavailable.html", error=str(e)), 502
    verifier, challenge = oidc.pkce_pair()
    import secrets
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    session["dha_oidc"] = {"state": state, "nonce": nonce, "verifier": verifier, "intent": intent,
                           "user_id": current_user.id if intent == "link" else None}
    return redirect(oidc.build_auth_url(cfg, doc, state, nonce, challenge))


@security_bp.route("/security/dha/callback")
@csrf.exempt
def dha_callback():
    cfg, st = current_app.config, session.pop("dha_oidc", None)
    if not st or request.args.get("state") != st["state"] or not request.args.get("code"):
        return redirect(url_for("auth.login"))
    try:
        doc = oidc.discover(cfg, _http())
        tok = oidc.exchange_code(cfg, doc, request.args["code"], st["verifier"], _http())
        claims = oidc.verify_id_token(tok["id_token"], cfg, doc, st["nonce"])
    except Exception as e:  # noqa: BLE001 — any failure must end in a safe refusal
        log_action(None, "dha_login_failed", details={"error": e.__class__.__name__}, ip_address=request.remote_addr)
        db.session.commit()
        return render_template("security/dha_unavailable.html", error="Digital Health ID sign-in failed."), 401
    subject = claims["sub"]
    if st["intent"] == "link" and st["user_id"]:
        user = db.session.get(User, st["user_id"])
        if not user or ExternalIdentity.query.filter_by(provider="dha", subject=subject).first():
            return render_template("security/dha_unavailable.html", error="That Digital Health ID is already linked or the session expired."), 409
        db.session.add(ExternalIdentity(user_id=user.id, provider="dha", subject=subject))
        log_action(user, "dha_linked", "User", user.id)
        db.session.commit()
        return redirect(url_for("security.mfa_setup"))
    ident = ExternalIdentity.query.filter_by(provider="dha", subject=subject).first()
    if not ident or not ident.user.is_active or ident.user.is_locked_out:
        log_action(None, "dha_login_unlinked", details={"subject_tail": subject[-4:]}, ip_address=request.remote_addr)
        db.session.commit()
        return render_template("security/dha_unavailable.html", error="No active MediCore account is linked to this Digital Health ID. Sign in with your password, then link it."), 403
    user = ident.user
    if mfa_service.is_enabled(user) and not cfg.get("DHA_OIDC_TRUST_MFA"):
        session.clear()
        session["mfa_pending_user_id"], session["mfa_pending_at"] = user.id, time.time()
        return redirect(url_for("security.mfa_verify"))
    user.register_successful_login()
    login_user(user)
    session.permanent = True
    log_action(user, "login", ip_address=request.remote_addr, details={"method": "dha"})
    db.session.commit()
    return redirect(url_for("main.dashboard"))
