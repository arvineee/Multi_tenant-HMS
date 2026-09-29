import datetime

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user

from app.decorators import permission_required
from app.models import Patient
from app.audit.service import log_view, log_denied, access_history
from app.consent import service
from app.consent.models import CONSENT_PURPOSES, AuthorisedPerson

consent_bp = Blueprint("consent", __name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _patient_or_403(patient_id, action="view"):
    """Load the patient or refuse. `action` is what was attempted, so a refused
    consent POST is recorded as such rather than as a refused view."""
    patient = Patient.query.get_or_404(patient_id)
    if patient.hospital_id not in current_user.accessible_hospital_ids():
        log_denied(current_user, action, patient.id, "outside accessible hospitals")
        abort(403)
    return patient


def _to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _row(c):
    if not c:
        return None
    out = {
        "granted": c.granted, "method": c.method, "recorded_at": c.recorded_at.isoformat(),
        "recorded_by": c.recorded_by.username if c.recorded_by else None,
    }
    ev = c.digital_evidence
    if ev:
        out["digital"] = {"signer": ev.signer_name, "role": ev.signer_role,
                          "confirmed_at": ev.confirmed_at.isoformat(),
                          "statement_sha256": ev.statement_sha256,
                          "signed": bool(ev.signature_png)}
    return out


@consent_bp.route("/patients/<int:patient_id>/consent", methods=["GET"])
@login_required
@permission_required("patient.view")
def consent_status(patient_id):
    patient = _patient_or_403(patient_id)
    log_view(current_user, patient, section="consent")
    return jsonify(
        purposes=CONSENT_PURPOSES,
        current={p: _row(c) for p, c in service.consent_summary(patient.id).items()},
        authorised_persons=[
            {"id": a.id, "name": a.full_name, "relationship": a.relationship,
             "scope": a.scope, "valid": a.is_valid}
            for a in AuthorisedPerson.query.filter_by(patient_id=patient.id, is_active=True).all()
        ],
    )


@consent_bp.route("/patients/<int:patient_id>/consent", methods=["POST"])
@login_required
@permission_required("consent.manage")
def consent_record(patient_id):
    patient = _patient_or_403(patient_id, "consent_record")
    data = request.get_json(silent=True) or request.form
    # A missing/blank "granted" must never be read as a withdrawal.
    if str(data.get("granted", "")).strip().lower() not in _TRUE | _FALSE:
        return jsonify(error="'granted' is required (true or false)."), 400
    method = (data.get("method") or "").strip().lower()
    if not method:
        return jsonify(error="Choose how the consent was given (written, verbal or digital)."), 400
    digital = None
    if method == "digital":
        digital = {
            "acknowledged": str(data.get("acknowledged", "")).strip().lower() in _TRUE,
            "signer_name": data.get("signer_name"),
            "signature_png": data.get("signature_png"),
            "ip_address": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
        }
    try:
        service.record_consent(
            current_user, patient, data.get("purpose"),
            str(data.get("granted")).strip().lower() in _TRUE,
            method=method,
            authorised_person_id=_to_int(data.get("authorised_person_id")),
            notes=data.get("notes"), digital=digital,
        )
    except service.ConsentError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@consent_bp.route("/patients/<int:patient_id>/authorised-persons", methods=["POST"])
@login_required
@permission_required("consent.manage")
def authorised_person_add(patient_id):
    patient = _patient_or_403(patient_id, "authorised_person_add")
    data = request.get_json(silent=True) or request.form
    valid_until = None
    if data.get("valid_until"):
        try:
            valid_until = datetime.datetime.strptime(data["valid_until"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify(error="valid_until must be YYYY-MM-DD"), 400
    try:
        ap = service.add_authorised_person(
            current_user, patient, data.get("full_name"), data.get("relationship"),
            data.get("national_id"), data.get("phone"), data.get("scope", "consent"), valid_until)
    except service.ConsentError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, id=ap.id)


@consent_bp.route("/patients/<int:patient_id>/access-history", methods=["GET"])
@login_required
@permission_required("audit.view")
def access_log(patient_id):
    """Who has viewed or changed this patient's record — for data-subject requests."""
    patient = _patient_or_403(patient_id, "access_history_view")
    log_view(current_user, patient, section="access-history")  # reading the trail is itself audited
    limit = min(max(_to_int(request.args.get("limit")) or 200, 1), 1000)
    return jsonify(entries=[
        {"when": e.timestamp.isoformat(), "user": e.user.username if e.user else None,
         "action": e.action, "outcome": e.outcome, "path": e.request_path}
        for e in access_history(patient.id, limit=limit)
    ])


@consent_bp.route("/patients/<int:patient_id>/authorised-persons/<int:person_id>/revoke", methods=["POST"])
@login_required
@permission_required("consent.manage")
def authorised_person_revoke(patient_id, person_id):
    """Stop treating this person as authorised. Consent decisions they already
    gave stay in the history; they just can't give new ones."""
    patient = _patient_or_403(patient_id, "authorised_person_revoke")
    ap = AuthorisedPerson.query.filter_by(id=person_id, patient_id=patient.id).first_or_404()
    if ap.is_active:
        service.revoke_authorised_person(current_user, ap)
    return jsonify(ok=True)
