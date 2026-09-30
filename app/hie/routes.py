import json

from flask import Blueprint, render_template, request, jsonify, abort, current_app
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.decorators import permission_required
from app.models import Hospital, log_action
from app.security.access import load_patient
from app.hie import service as svc
from app.hie.models import HieTransaction, HieInbound

hie_bp = Blueprint("hie", __name__, template_folder="../templates/hie")


@hie_bp.route("/hie")
@login_required
@permission_required("hie.manage")
def transactions():
    ids = current_user.accessible_hospital_ids()
    rows = HieTransaction.query.filter(HieTransaction.hospital_id.in_(ids)).order_by(HieTransaction.id.desc()).limit(200).all()
    return render_template("hie/transactions.html", rows=rows, mode=current_app.config["HIE_MODE"],
                           configured=svc.client().configured())


@hie_bp.route("/hie/<int:tx_id>")
@login_required
@permission_required("hie.manage")
def transaction_detail(tx_id):
    tx = db.session.get(HieTransaction, tx_id) or abort(404)
    if tx.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    log_action(current_user, "view", "HieTransaction", tx.id, patient_id=tx.patient_id)
    db.session.commit()
    pretty = json.dumps(json.loads(tx.bundle_json), indent=2)[:60000] if tx.bundle_json else ""
    return render_template("hie/transaction.html", tx=tx, pretty=pretty)


@hie_bp.route("/hie/patients/<int:patient_id>/send", methods=["POST"])
@login_required
@permission_required("hie.manage")
def send_patient(patient_id):
    patient = load_patient(patient_id, write=True, action="share_hie")
    tx = svc.send_patient_summary(current_user, patient)
    return jsonify(success=tx.status in ("Sent", "Mock"), status=tx.status, detail=tx.response_excerpt, id=tx.id)


@hie_bp.route("/hie/<int:tx_id>/retry", methods=["POST"])
@login_required
@permission_required("hie.manage")
def retry(tx_id):
    tx = db.session.get(HieTransaction, tx_id) or abort(404)
    if tx.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    try:
        tx = svc.retry(current_user, tx)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    return jsonify(success=tx.status in ("Sent", "Mock"), status=tx.status)


@hie_bp.route("/hie/inbox")
@login_required
@permission_required("hie.manage")
def inbox():
    ids = current_user.accessible_hospital_ids()
    rows = HieInbound.query.filter(HieInbound.hospital_id.in_(ids)).order_by(HieInbound.id.desc()).limit(200).all()
    return render_template("hie/inbox.html", rows=rows)


@hie_bp.route("/hie/inbox/<int:row_id>", methods=["GET", "POST"])
@login_required
@permission_required("hie.manage")
def inbox_item(row_id):
    row = db.session.get(HieInbound, row_id) or abort(404)
    if row.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    if request.method == "POST":
        action = (request.get_json(silent=True) or request.form).get("action")
        if action not in ("Applied", "Rejected"):
            return jsonify(success=False, error="Invalid action."), 400
        row.status, row.reviewed_by_id = action, current_user.id
        import datetime
        row.reviewed_at = datetime.datetime.utcnow()
        log_action(current_user, "hie_review", "HieInbound", row.id, {"outcome": action}, patient_id=row.patient_id)
        db.session.commit()
        return jsonify(success=True)
    log_action(current_user, "view", "HieInbound", row.id, patient_id=row.patient_id)
    db.session.commit()
    return render_template("hie/inbox_item.html", row=row, pretty=json.dumps(json.loads(row.bundle_json), indent=2)[:60000])


@hie_bp.route("/hie/inbound", methods=["POST"])
def inbound():
    """Receiving endpoint for the HIE. Authenticated by HMAC-SHA256 of the raw
    body in the X-Signature header (secret: HIE_INBOUND_SECRET). Stored for a
    clinician to review; nothing is applied to a chart automatically."""
    cfg = current_app.config
    if cfg["HIE_MODE"] not in ("mock", "live") or not cfg["HIE_INBOUND_SECRET"]:
        return jsonify(error="inbound not enabled"), 404
    body = request.get_data(cache=False, as_text=False)
    if len(body) > 5_000_000:
        return jsonify(error="too large"), 413
    if not svc.verify_signature(cfg["HIE_INBOUND_SECRET"], body, request.headers.get("X-Signature", "")):
        log_action(None, "hie_inbound_rejected", details={"reason": "bad signature"}, ip_address=request.remote_addr, outcome="denied")
        db.session.commit()
        return jsonify(error="invalid signature"), 401
    try:
        bundle = json.loads(body)
        hospitals = [h.id for h in Hospital.query.filter_by(is_active=True).all()]
        default = Hospital.query.filter_by(is_active=True).order_by(Hospital.id).first()
        row = svc.store_inbound(bundle, default.id if default else None, hospitals)
    except (ValueError, TypeError) as e:
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(status="received", id=row.id), 201


@hie_bp.route("/hie/patients/<int:patient_id>/pull", methods=["POST"])
@login_required
@permission_required("hie.manage")
def pull(patient_id):
    """Pull from the HIE by the patient's Client Registry / national ID (needs live mode)."""
    patient = load_patient(patient_id, write=True, action="pull_hie")
    from app.consent.guards import consent_block_reason
    reason = consent_block_reason(patient.id, "hie")
    if reason:
        return jsonify(success=False, error=reason), 403
    ident = patient.dha_client_id or patient.national_id
    if not ident:
        return jsonify(success=False, error="Add the patient's Client Registry ID or national ID first."), 400
    system = "client-registry" if patient.dha_client_id else "national-id"
    try:
        bundle = svc.client().pull(f"{current_app.config['HIE_IDENTIFIER_BASE']}/{system}", ident)
        row = svc.store_inbound(bundle, patient.hospital_id, [patient.hospital_id], source="pull")
        row.patient_id = patient.id
    except Exception as e:  # noqa: BLE001
        return jsonify(success=False, error=str(e)[:200]), 502
    log_action(current_user, "hie_pull", "HieInbound", row.id, patient_id=patient.id)
    db.session.commit()
    return jsonify(success=True, id=row.id)


# ---------------------------------------------------------------------------
# DHA registry / eligibility / OTP consent
# ---------------------------------------------------------------------------
from app.decorators import permission_required as _perm  # noqa: E402


def _body():
    return request.get_json(silent=True) or request.form


def _dha_patient(patient_id):
    patient = load_patient(patient_id, write=True, action="dha_lookup")
    return patient


@hie_bp.route("/hie/patients/<int:patient_id>/dha")
@login_required
@_perm("dha.lookup")
def dha_page(patient_id):
    patient = load_patient(patient_id, section="dha-lookup")
    from app.consent.guards import consent_block_reason
    from app.hie.models import DhaLookup
    return render_template("hie/dha_lookup.html", patient=patient, ids=[l for l, _ in svc.patient_identifiers(patient)],
                           consent_problem=consent_block_reason(patient.id, svc.DHA_CONSENT_PURPOSE),
                           live=current_app.config["HIE_MODE"] == "live",
                           hospital_missing_fr=not patient.hospital.fr_code,
                           history=DhaLookup.query.filter_by(patient_id=patient.id).order_by(DhaLookup.id.desc()).limit(15).all())


def _run(patient_id, kind, build):
    patient = _dha_patient(patient_id)
    d = _body()
    try:
        fn = build(patient, d)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    ok_, data = svc.dha_call(current_user, patient, kind, fn)
    return (jsonify(success=True, data=data) if ok_ else jsonify(success=False, error=data)), (200 if ok_ else 502)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/search", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_search(patient_id):
    def build(p, d):
        t, v, _ = svc.resolve_identifier(p, "search", d.get("id_label"))
        return lambda c: c.search_patient(v, t)
    return _run(patient_id, "search", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/eligibility", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_eligibility(patient_id):
    """DHA recommends the Client Registry ID when we hold it; resolve_identifier puts it first."""
    def build(p, d):
        t, v, _ = svc.resolve_identifier(p, "eligibility", d.get("id_label"))
        return lambda c: c.eligibility(v, t)
    patient = _dha_patient(patient_id)
    try:
        fn = build(patient, _body())
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    ok_, data = svc.dha_call(current_user, patient, "eligibility", fn)
    if not ok_:
        return jsonify(success=False, error=data), 502
    from app.hie.dha_client import eligibility_flags, otp_allowed, is_pomsf
    flags = eligibility_flags(data)
    allowed, why = otp_allowed(flags)
    return jsonify(success=True, data=data, flags=flags, otp_allowed=allowed, otp_blocked_reason=why,
                   pomsf=is_pomsf(flags["schemes"]))


def _need(d, key):
    v = (d.get(key) or "").strip() if isinstance(d.get(key), str) else d.get(key)
    if not v:
        raise ValueError("Missing " + key.replace("_", " ") + ".")
    return v


@hie_bp.route("/hie/patients/<int:patient_id>/dha/benefits", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_benefits(patient_id):
    def build(p, d):
        pid = _need(d, "dha_patient_id")
        return lambda c: c.benefits(pid)
    return _run(patient_id, "benefits", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/sub-benefits", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_sub_benefits(patient_id):
    def build(p, d):
        pid, parent = _need(d, "dha_patient_id"), _need(d, "parent_benefit_code")
        return lambda c: c.sub_benefits(pid, parent)
    return _run(patient_id, "sub_benefits", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/interventions", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_interventions(patient_id):
    def build(p, d):
        pid, code = _need(d, "dha_patient_id"), _need(d, "sub_benefit_code")
        return lambda c: c.interventions(pid, code)
    return _run(patient_id, "interventions", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/otp/contacts", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_otp_contacts(patient_id):
    def build(p, d):
        pid = _need(d, "dha_patient_id")
        return lambda c: c.otp_contacts(pid)
    return _run(patient_id, "otp_contacts", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/otp/send", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_otp_send(patient_id):
    def build(p, d):
        pid = _need(d, "dha_patient_id")
        raw = d.get("intervention_codes")
        codes = [c.strip() for c in raw.split(",")] if isinstance(raw, str) else list(raw or [])
        codes = [c for c in codes if c]
        if not codes:
            raise ValueError("Enter the intervention code(s) the patient is consenting to.")
        cid = d.get("contact_id") or None
        return lambda c: c.send_otp(pid, codes, cid)
    return _run(patient_id, "otp_send", build)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/save-client-id", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_save_client_id(patient_id):
    """Saves the DHA patient id the user has confirmed as the right person."""
    patient = _dha_patient(patient_id)
    val = (_body().get("dha_patient_id") or "").strip()
    if not val or len(val) > 60:
        return jsonify(success=False, error="Enter the DHA patient id (max 60 characters)."), 400
    clash = type(patient).query.filter(type(patient).dha_client_id == val, type(patient).id != patient.id).first()
    if clash:
        return jsonify(success=False, error="That DHA id is already saved on another patient."), 409
    patient.dha_client_id = val
    log_action(current_user, "update", "Patient", patient.id, {"dha_client_id_set": True}, patient_id=patient.id)
    db.session.commit()
    return jsonify(success=True)


@hie_bp.route("/hie/patients/<int:patient_id>/dha/guess-id", methods=["POST"])
@login_required
@_perm("dha.lookup")
def dha_guess_id(patient_id):
    _dha_patient(patient_id)
    return jsonify(success=True, dha_patient_id=svc.extract_dha_patient_id(_body().get("data")))
