import datetime

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.level_policy import hospital_allows_inpatient, radiology_test_allowed
from app.models import (
    Visit, Triage, Consultation, ConsultationDiagnosis, Admission, Ward, Bed, LabOrder, RadiologyOrder,
    LabTest, RadiologyTest, log_action, TRIAGE_PRIORITIES,
    Prescription, PrescriptionItem, Drug,
)

clinical_bp = Blueprint("clinical", __name__, template_folder="../templates/clinical")


def _visit_or_403(visit_id):
    visit = Visit.query.get_or_404(visit_id)
    if visit.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return visit


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/triage", methods=["POST"])
@login_required
@permission_required("triage.create")
def record_triage(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form

    priority = data.get("priority", "Normal")
    if priority not in TRIAGE_PRIORITIES:
        priority = "Normal"

    triage = visit.triage or Triage(hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id)
    triage.temperature_c = _to_float(data.get("temperature_c"))
    triage.pulse_bpm = _to_int(data.get("pulse_bpm"))
    triage.bp_systolic = _to_int(data.get("bp_systolic"))
    triage.bp_diastolic = _to_int(data.get("bp_diastolic"))
    triage.respiratory_rate = _to_int(data.get("respiratory_rate"))
    triage.spo2_percent = _to_int(data.get("spo2_percent"))
    triage.weight_kg = _to_float(data.get("weight_kg"))
    triage.height_cm = _to_float(data.get("height_cm"))
    triage.priority = priority
    triage.notes = data.get("notes")
    triage.recorded_by_id = current_user.id

    db.session.add(triage)

    if visit.status == "Waiting":
        visit.status = "Triaged"

    db.session.flush()  # so triage.id is populated before we log it
    log_action(current_user, "create", "Triage", triage.id, {"visit_id": visit.id, "priority": priority})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Triage relief medication — a nurse can give a quick dose of something
# for symptomatic relief (fever, pain, nausea...) before the doctor has
# seen the patient. Deliberately restricted to drugs the hospital has
# flagged Drug.is_triage_relief, rather than any drug in the formulary —
# this is comfort-care under protocol, not a substitute for a doctor's
# prescription. Draws real stock and bills like any other dispense.
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/triage-relief-medication", methods=["POST"])
@login_required
@permission_required("triage.create")
def give_triage_relief_medication(visit_id):
    visit = _visit_or_403(visit_id)
    if visit.status not in ("Waiting", "Triaged"):
        return jsonify(success=False, error="This visit has already moved past triage."), 400

    data = request.get_json(silent=True) or request.form
    drug_id = data.get("drug_id")
    drug = Drug.query.get(drug_id) if drug_id else None
    if not drug or drug.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Select a drug."), 400
    if not drug.is_triage_relief:
        return jsonify(success=False, error=f"{drug.name} isn't approved for nurse-given relief at triage — needs a doctor's order."), 400

    dosage = (data.get("dosage") or "").strip()
    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    if quantity <= 0:
        quantity = 1

    prescription = Prescription(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
        doctor_id=current_user.id, is_triage_order=True,
    )
    db.session.add(prescription)
    db.session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id, drug_id=drug.id,
        dosage=dosage, frequency="STAT", instructions=data.get("notes"),
        quantity_prescribed=quantity,
    )
    db.session.add(item)
    db.session.flush()

    from app.pharmacy.routes import _draw_stock_fefo
    try:
        charged_amount = _draw_stock_fefo(visit.hospital_id, drug.id, quantity, item.id, current_user.id)
    except ValueError as e:
        db.session.rollback()
        return jsonify(success=False, error=f"{e} ({drug.name})"), 400

    item.quantity_dispensed = quantity
    item.billed_amount = charged_amount
    item.status = "Dispensed"

    log_action(current_user, "create", "Prescription", prescription.id, {
        "visit_id": visit.id, "triage_relief": True, "drug": drug.name, "quantity": quantity,
    })
    db.session.commit()
    return jsonify(success=True, prescription_id=prescription.id)


# ---------------------------------------------------------------------------
# Consultation (+ optional inpatient admission)
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/consultation", methods=["POST"])
@login_required
@permission_required("consultation.create")
def record_consultation(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form
    action = data.get("action", "finalize")  # 'draft' = save & keep seeing patient, 'finalize' = close visit

    consultation = visit.consultation or Consultation(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id
    )
    consultation.doctor_id = current_user.id
    consultation.chief_complaint = data.get("chief_complaint")
    consultation.history_of_presenting_illness = data.get("history_of_presenting_illness")
    consultation.past_medical_history = data.get("past_medical_history")
    consultation.past_surgical_history = data.get("past_surgical_history")
    consultation.drug_history = data.get("drug_history")
    consultation.family_social_history = data.get("family_social_history")
    consultation.review_of_systems = data.get("review_of_systems")
    consultation.examination_notes = data.get("examination_notes")
    consultation.diagnosis_code_id = data.get("diagnosis_code_id") or None
    consultation.diagnosis_notes = data.get("diagnosis_notes")
    consultation.treatment_plan = data.get("treatment_plan")
    consultation.follow_up_date = _parse_date(data.get("follow_up_date"))
    db.session.add(consultation)
    db.session.flush()  # need consultation.id before writing additional diagnoses

    # additional/secondary diagnoses: full-replace each save, same as the
    # rest of the consultation form re-saving its whole state each time
    additional = data.get("additional_diagnoses") or []
    ConsultationDiagnosis.query.filter_by(consultation_id=consultation.id).delete()
    seen_codes = set()
    for item in additional:
        code_id = item.get("diagnosis_code_id") if isinstance(item, dict) else None
        if not code_id or code_id == consultation.diagnosis_code_id or code_id in seen_codes:
            continue
        seen_codes.add(code_id)
        db.session.add(ConsultationDiagnosis(
            consultation_id=consultation.id, diagnosis_code_id=code_id,
            notes=item.get("notes") if isinstance(item, dict) else None,
        ))

    if visit.status == "Triaged":
        visit.status = "In Consultation"

    if action == "draft":
        log_action(current_user, "update", "Consultation", consultation.id, {"visit_id": visit.id, "draft": True})
        db.session.commit()
        return jsonify(success=True, finalized=False)

    # --- finalize: diagnosis is required to close a consultation out ---
    if not consultation.diagnosis_code_id:
        db.session.commit()  # keep whatever notes/orders they've already entered
        return jsonify(success=False, error="Select a diagnosis before completing the consultation."), 400

    admit_patient = str(data.get("admit_patient", "")).lower() in ("1", "true", "on", "yes")

    # A patient can be admitted straight from an Outpatient consultation —
    # there's no need to close the OPD file and re-register them as a
    # fresh Inpatient visit first. Admitting here simply converts the
    # existing visit over to Inpatient and carries the OPD consultation
    # forward as the admitting diagnosis/clerking.
    if admit_patient:
        if not hospital_allows_inpatient(visit.hospital):
            return jsonify(success=False, error=f"{visit.hospital.level} facilities don't offer inpatient admission."), 400
        ward_id = data.get("ward_id")
        if not ward_id:
            return jsonify(success=False, error="Select a ward to admit this patient."), 400
        ward = Ward.query.get(ward_id)
        if not ward or ward.hospital_id != visit.hospital_id:
            return jsonify(success=False, error="Invalid ward."), 400

        bed = None
        bed_id = data.get("bed_id")
        if ward.beds:
            if not bed_id:
                return jsonify(success=False, error="Select a specific bed in that ward."), 400
            bed = Bed.query.get(bed_id)
            if not bed or bed.ward_id != ward.id:
                return jsonify(success=False, error="Invalid bed for that ward."), 400
            if bed.status != "Available":
                return jsonify(success=False, error="That bed isn't available."), 400
        elif ward.available_beds <= 0:
            return jsonify(success=False, error=f"{ward.name} has no available beds."), 400

        admission = visit.admission or Admission(
            hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id
        )
        admission.ward_id = ward.id
        admission.bed_id = bed.id if bed else None
        admission.bed_number = bed.label if bed else data.get("bed_number")
        admission.admitting_doctor_id = current_user.id
        admission.admitting_consultation_id = consultation.id
        admission.expected_discharge_date = _parse_date(data.get("expected_discharge_date"))
        admission.status = "Active"

        # Seed the insurance authorization ceiling from the patient's
        # scheme default (billing can override it once the insurer's
        # actual authorization letter comes through). Cash patients get
        # no limit at all — nothing to enforce there.
        scheme = visit.patient.insurance_scheme
        if scheme and scheme.default_credit_limit:
            admission.insurance_authorized_amount = scheme.default_credit_limit

        if bed:
            bed.status = "Occupied"
        db.session.add(admission)
        visit.visit_type = "Inpatient"  # convert the visit over, whichever type it started as
        visit.status = "Admitted"
        log_action(current_user, "create", "Admission", None, {"visit_id": visit.id, "ward": ward.name})
    else:
        visit.status = "Completed"
        visit.closed_at = datetime.datetime.utcnow()

    log_action(current_user, "create", "Consultation", consultation.id, {"visit_id": visit.id, "finalized": True})
    db.session.commit()
    return jsonify(success=True, finalized=True)


# ---------------------------------------------------------------------------
# Discharge an admitted inpatient
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/discharge", methods=["POST"])
@login_required
@permission_required("consultation.create")
def discharge_patient(visit_id):
    visit = _visit_or_403(visit_id)
    if not visit.admission or visit.admission.status != "Active":
        return jsonify(success=False, error="This visit has no active admission to discharge."), 400

    data = request.get_json(silent=True) or request.form
    visit.admission.status = "Discharged"
    visit.admission.actual_discharge_date = datetime.datetime.utcnow()
    visit.admission.notes = data.get("notes", visit.admission.notes)
    if visit.admission.bed:
        visit.admission.bed.status = "Available"
    visit.status = "Discharged"
    visit.closed_at = datetime.datetime.utcnow()

    log_action(current_user, "update", "Admission", visit.admission.id, {"discharged": True})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Lab orders — doctor orders mid-consultation, lab tech resolves independently
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/lab-orders", methods=["POST"])
@login_required
@permission_required("lab.order")
def order_lab_test(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form
    lab_test_id = data.get("lab_test_id")
    if not lab_test_id:
        return jsonify(success=False, error="Select a lab test."), 400

    lab_test = LabTest.query.get(lab_test_id)
    if not lab_test or lab_test.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Invalid lab test."), 400

    order = LabOrder(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
        lab_test_id=lab_test_id, clinical_notes=data.get("clinical_notes"),
        ordered_by_id=current_user.id,
    )
    db.session.add(order)

    if visit.status == "Triaged":
        visit.status = "In Consultation"

    db.session.flush()  # so order.id is populated before we log it
    log_action(current_user, "create", "LabOrder", order.id, {"visit_id": visit.id})
    db.session.commit()
    return jsonify(success=True, id=order.id)


@clinical_bp.route("/lab-orders/<int:order_id>/result", methods=["POST"])
@login_required
@permission_required("lab.result")
def enter_lab_result(order_id):
    order = LabOrder.query.get_or_404(order_id)
    if order.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)

    data = request.get_json(silent=True) or request.form
    order.result_value = data.get("result_value")
    order.result_notes = data.get("result_notes")
    order.status = "Result Ready"
    order.resulted_by_id = current_user.id
    order.resulted_at = datetime.datetime.utcnow()

    from app.billing.routes import sync_admission_insurance_flag
    sync_admission_insurance_flag(order.visit)

    log_action(current_user, "update", "LabOrder", order.id, {"status": "Result Ready"})
    db.session.commit()
    return jsonify(success=True, insurance_limit_reached=bool(order.visit.admission and order.visit.admission.insurance_limit_reached))


@clinical_bp.route("/lab-orders/<int:order_id>/status", methods=["POST"])
@login_required
@permission_required("lab.result")
def update_lab_status(order_id):
    order = LabOrder.query.get_or_404(order_id)
    if order.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    data = request.get_json(silent=True) or request.form
    status = data.get("status")
    from app.models import LAB_ORDER_STATUSES
    if status not in LAB_ORDER_STATUSES:
        return jsonify(success=False, error="Invalid status."), 400
    order.status = status
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Radiology orders — same pattern as lab
# ---------------------------------------------------------------------------

@clinical_bp.route("/visits/<int:visit_id>/radiology-orders", methods=["POST"])
@login_required
@permission_required("radiology.order")
def order_radiology_test(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form
    radiology_test_id = data.get("radiology_test_id")
    if not radiology_test_id:
        return jsonify(success=False, error="Select a radiology test."), 400

    radiology_test = RadiologyTest.query.get(radiology_test_id)
    if not radiology_test or radiology_test.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Invalid radiology test."), 400
    if not radiology_test_allowed(visit.hospital, radiology_test):
        return jsonify(success=False, error=f"{radiology_test.name} isn't available at a {visit.hospital.level} facility."), 400

    order = RadiologyOrder(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
        radiology_test_id=radiology_test_id, clinical_notes=data.get("clinical_notes"),
        ordered_by_id=current_user.id,
    )
    db.session.add(order)

    if visit.status == "Triaged":
        visit.status = "In Consultation"

    db.session.flush()  # so order.id is populated before we log it
    log_action(current_user, "create", "RadiologyOrder", order.id, {"visit_id": visit.id})
    db.session.commit()
    return jsonify(success=True, id=order.id)


@clinical_bp.route("/radiology-orders/<int:order_id>/report", methods=["POST"])
@login_required
@permission_required("radiology.report")
def enter_radiology_report(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)
    if order.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)

    data = request.get_json(silent=True) or request.form
    order.findings = data.get("findings")
    order.status = "Report Ready"
    order.reported_by_id = current_user.id
    order.reported_at = datetime.datetime.utcnow()

    from app.billing.routes import sync_admission_insurance_flag
    sync_admission_insurance_flag(order.visit)

    log_action(current_user, "update", "RadiologyOrder", order.id, {"status": "Report Ready"})
    db.session.commit()
    return jsonify(success=True, insurance_limit_reached=bool(order.visit.admission and order.visit.admission.insurance_limit_reached))


@clinical_bp.route("/radiology-orders/<int:order_id>/status", methods=["POST"])
@login_required
@permission_required("radiology.report")
def update_radiology_status(order_id):
    order = RadiologyOrder.query.get_or_404(order_id)
    if order.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    data = request.get_json(silent=True) or request.form
    status = data.get("status")
    from app.models import RADIOLOGY_ORDER_STATUSES
    if status not in RADIOLOGY_ORDER_STATUSES:
        return jsonify(success=False, error="Invalid status."), 400
    order.status = status
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Worklists — let staff jump between patients instead of being stuck on one
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {"Emergency": 0, "Urgent": 1, "Normal": 2}


@clinical_bp.route("/queue", methods=["GET"])
@login_required
def patient_queue():
    """Each department sees only the work that's actually theirs:
    a nurse sees who still needs triage, a doctor sees who's ready for
    (or already mid-) consultation. Nobody gets a firehose of every
    active visit in the building."""
    hospital_ids = current_user.accessible_hospital_ids()

    if current_user.has_permission("consultation.create"):
        statuses = ["Triaged", "In Consultation"]
        title = "Consultation Queue"
    elif current_user.has_permission("triage.create"):
        statuses = ["Waiting"]
        title = "Triage Queue"
    else:
        abort(403)

    visits = Visit.query.filter(
        Visit.hospital_id.in_(hospital_ids),
        Visit.status.in_(statuses),
    ).order_by(Visit.created_at.asc()).all()

    def sort_key(v):
        priority = v.triage.priority if v.triage else "Normal"
        return (PRIORITY_ORDER.get(priority, 2), v.created_at)

    visits = sorted(visits, key=sort_key)
    return render_template("clinical/queue.html", visits=visits, title=title)


@clinical_bp.route("/lab/worklist", methods=["GET"])
@login_required
@permission_required("lab.result")
def lab_worklist():
    hospital_ids = current_user.accessible_hospital_ids()
    orders = LabOrder.query.filter(
        LabOrder.hospital_id.in_(hospital_ids),
        LabOrder.status.in_(["Ordered", "Sample Collected"]),
    ).order_by(LabOrder.ordered_at.asc()).all()
    return render_template("clinical/lab_worklist.html", orders=orders)


@clinical_bp.route("/radiology/worklist", methods=["GET"])
@login_required
@permission_required("radiology.report")
def radiology_worklist():
    hospital_ids = current_user.accessible_hospital_ids()
    orders = RadiologyOrder.query.filter(
        RadiologyOrder.hospital_id.in_(hospital_ids),
        RadiologyOrder.status.in_(["Ordered", "In Progress"]),
    ).order_by(RadiologyOrder.ordered_at.asc()).all()
    return render_template("clinical/radiology_worklist.html", orders=orders)
