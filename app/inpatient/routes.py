import datetime

from flask import Blueprint, request, jsonify, abort, render_template
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required, any_permission_required
from app.models import (
    Admission, Ward, Bed, InpatientVitals, NursingNote, WardTransfer, log_action,
    DoctorReview, CarePlan, CARE_PLAN_STATUSES, MonitoringEntry, MONITORING_CHART_TYPES,
    ProcedureOrder, PROCEDURE_ORDER_STATUSES,
    MedicalDocument, DOCUMENT_TYPE_PREFIXES,
)

inpatient_bp = Blueprint("inpatient", __name__, template_folder="../templates/inpatient")


def _admission_or_403(admission_id):
    admission = Admission.query.get_or_404(admission_id)
    if admission.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return admission


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Bed management (Admin/Manager sets up a ward's individual beds)
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admin/wards/<int:ward_id>/beds", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def add_bed(ward_id):
    ward = Ward.query.get_or_404(ward_id)
    if ward.hospital_id != current_user.hospital_id:
        abort(403)

    data = request.get_json(silent=True) or request.form
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify(success=False, error="Bed label is required."), 400
    if Bed.query.filter_by(ward_id=ward.id, label=label).first():
        return jsonify(success=False, error="That bed label already exists in this ward."), 400

    bed = Bed(ward_id=ward.id, label=label, status="Available")
    db.session.add(bed)
    db.session.commit()
    return jsonify(success=True, id=bed.id)


@inpatient_bp.route("/beds/<int:bed_id>/status", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def update_bed_status(bed_id):
    bed = Bed.query.get_or_404(bed_id)
    if bed.ward.hospital_id != current_user.hospital_id:
        abort(403)

    data = request.get_json(silent=True) or request.form
    status = data.get("status")
    from app.models import BED_STATUSES
    if status not in BED_STATUSES:
        return jsonify(success=False, error="Invalid bed status."), 400
    if status != "Available" and Admission.query.filter_by(bed_id=bed.id, status="Active").first():
        return jsonify(success=False, error="This bed currently has an active patient — discharge or transfer them first."), 400

    bed.status = status
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Ward transfer — move an active admission to a different ward/bed
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/transfer", methods=["POST"])
@login_required
@permission_required("consultation.create")
def transfer_ward(admission_id):
    admission = _admission_or_403(admission_id)
    if admission.status != "Active":
        return jsonify(success=False, error="Only an active admission can be transferred."), 400

    data = request.get_json(silent=True) or request.form
    to_ward_id = data.get("to_ward_id")
    to_bed_id = data.get("to_bed_id") or None
    reason = data.get("reason")

    if not to_ward_id:
        return jsonify(success=False, error="Select a ward to transfer to."), 400
    to_ward = Ward.query.get(to_ward_id)
    if not to_ward or to_ward.hospital_id != admission.hospital_id:
        return jsonify(success=False, error="Invalid ward."), 400

    to_bed = None
    if to_bed_id:
        to_bed = Bed.query.get(to_bed_id)
        if not to_bed or to_bed.ward_id != to_ward.id:
            return jsonify(success=False, error="Invalid bed for that ward."), 400
        if to_bed.status != "Available":
            return jsonify(success=False, error="That bed isn't available."), 400
    elif to_ward.beds:
        return jsonify(success=False, error="Select a specific bed in that ward."), 400
    elif to_ward.available_beds <= 0:
        return jsonify(success=False, error=f"{to_ward.name} has no available beds."), 400

    transfer = WardTransfer(
        admission_id=admission.id,
        from_ward_id=admission.ward_id, to_ward_id=to_ward.id,
        from_bed_id=admission.bed_id, to_bed_id=to_bed.id if to_bed else None,
        reason=reason, transferred_by_id=current_user.id,
    )
    db.session.add(transfer)

    # free the old bed, occupy the new one
    if admission.bed:
        admission.bed.status = "Available"
    if to_bed:
        to_bed.status = "Occupied"

    admission.ward_id = to_ward.id
    admission.bed_id = to_bed.id if to_bed else None
    admission.bed_number = to_bed.label if to_bed else None

    log_action(current_user, "update", "Admission", admission.id, {"transferred_to_ward": to_ward.name})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Inpatient vitals — repeated nursing-round observations over the stay
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/vitals", methods=["POST"])
@login_required
@permission_required("triage.create")
def record_inpatient_vitals(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form

    vitals = InpatientVitals(
        admission_id=admission.id,
        temperature_c=_to_float(data.get("temperature_c")),
        pulse_bpm=_to_int(data.get("pulse_bpm")),
        bp_systolic=_to_int(data.get("bp_systolic")),
        bp_diastolic=_to_int(data.get("bp_diastolic")),
        respiratory_rate=_to_int(data.get("respiratory_rate")),
        spo2_percent=_to_int(data.get("spo2_percent")),
        notes=data.get("notes"),
        recorded_by_id=current_user.id,
    )
    db.session.add(vitals)
    db.session.commit()
    return jsonify(success=True, id=vitals.id)


# ---------------------------------------------------------------------------
# Nursing notes — free-text ward-round entries over the stay
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/nursing-notes", methods=["POST"])
@login_required
@permission_required("triage.create")
def add_nursing_note(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify(success=False, error="Note can't be empty."), 400

    entry = NursingNote(admission_id=admission.id, note=note, author_id=current_user.id)
    db.session.add(entry)
    db.session.commit()
    return jsonify(success=True, id=entry.id)


# ---------------------------------------------------------------------------
# Ward procedures/services — anything billable that isn't already covered
# by pharmacy/lab/radiology (dressing, catheter insertion, oxygen therapy,
# minor procedure fees...). Only charged once marked Done, same billing
# philosophy as lab/radiology results elsewhere in the app.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/procedures", methods=["POST"])
@login_required
@any_permission_required("consultation.create", "triage.create")
def order_procedure(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(success=False, error="Enter what procedure or service this is."), 400
    price = _to_float(data.get("unit_price"))
    if price is None or price < 0:
        return jsonify(success=False, error="Enter a valid price."), 400
    quantity = _to_float(data.get("quantity")) or 1

    procedure = ProcedureOrder(
        admission_id=admission.id, name=name, notes=data.get("notes"),
        quantity=quantity, unit_price=price, ordered_by_id=current_user.id,
    )
    mark_done = str(data.get("mark_done", "")).lower() in ("1", "true", "on", "yes")
    if mark_done:
        procedure.status = "Done"
        procedure.performed_by_id = current_user.id
        procedure.performed_at = datetime.datetime.utcnow()

    db.session.add(procedure)
    db.session.flush()
    if mark_done:
        from app.billing.routes import sync_admission_insurance_flag
        sync_admission_insurance_flag(admission.visit)

    log_action(current_user, "create", "ProcedureOrder", procedure.id, {"admission_id": admission.id, "name": name})
    db.session.commit()
    return jsonify(success=True, id=procedure.id,
                    insurance_limit_reached=admission.insurance_limit_reached)


@inpatient_bp.route("/procedures/<int:procedure_id>/complete", methods=["POST"])
@login_required
@any_permission_required("consultation.create", "triage.create")
def complete_procedure(procedure_id):
    procedure = ProcedureOrder.query.get_or_404(procedure_id)
    admission = procedure.admission
    if admission.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    if procedure.status != "Ordered":
        return jsonify(success=False, error="Only an ordered procedure can be marked done."), 400

    procedure.status = "Done"
    procedure.performed_by_id = current_user.id
    procedure.performed_at = datetime.datetime.utcnow()

    from app.billing.routes import sync_admission_insurance_flag
    sync_admission_insurance_flag(admission.visit)

    log_action(current_user, "update", "ProcedureOrder", procedure.id, {"status": "Done"})
    db.session.commit()
    return jsonify(success=True, insurance_limit_reached=admission.insurance_limit_reached)


@inpatient_bp.route("/procedures/<int:procedure_id>/cancel", methods=["POST"])
@login_required
@permission_required("consultation.create")
def cancel_procedure(procedure_id):
    procedure = ProcedureOrder.query.get_or_404(procedure_id)
    if procedure.admission.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    if procedure.status == "Done":
        return jsonify(success=False, error="This was already performed and billed — it can't be cancelled."), 400
    procedure.status = "Cancelled"
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# The inpatient admission workspace — one page per stay bringing together
# doctor reviews, cardex/care plan, vitals, monitoring charts, nursing
# notes, transfers, billing/insurance status and discharge, so ward staff
# never have to bounce between the OPD-style visit page and separate
# scattered modals to run a stay.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>", methods=["GET"])
@login_required
@any_permission_required("consultation.create", "triage.create")
def admission_workspace(admission_id):
    admission = _admission_or_403(admission_id)
    wards = Ward.query.filter_by(hospital_id=admission.hospital_id, is_active=True).all()

    def _series(chart_type):
        entries = sorted(
            [m for m in admission.monitoring_entries if m.chart_type == chart_type],
            key=lambda m: m.recorded_at,
        )[-20:]
        return _sparkline(entries)

    chart_series = {t: _series(t) for t in ("Intake", "Output", "Blood Sugar")}

    return render_template(
        "inpatient/admission.html", admission=admission, wards=wards,
        monitoring_chart_types=MONITORING_CHART_TYPES, care_plan_statuses=CARE_PLAN_STATUSES,
        chart_series=chart_series,
    )


def _sparkline(entries, width=280, height=56, pad=6):
    """Build a dependency-free SVG polyline from a list of MonitoringEntry
    rows, oldest first. No charting library needed — this is the app's
    only visual chart today, so a CDN dependency isn't worth it just for
    two small trend lines."""
    if len(entries) < 2:
        return {"entries": entries, "points": None}
    values = [e.value for e in entries]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = (width - 2 * pad) / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    return {"entries": entries, "points": " ".join(points), "width": width, "height": height, "lo": lo, "hi": hi}


# ---------------------------------------------------------------------------
# Ward census — who's in which bed, right now, across every active ward.
# The consolidated view that was previously missing entirely: bed
# occupancy was computed for the dashboard stat card but never actually
# browsable ward-by-ward.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/wards/census", methods=["GET"])
@login_required
@any_permission_required("consultation.create", "triage.create")
def ward_census():
    hospital_ids = current_user.accessible_hospital_ids()
    wards = (
        Ward.query.filter(Ward.hospital_id.in_(hospital_ids), Ward.is_active == True)  # noqa: E712
        .order_by(Ward.name).all()
    )
    active_admissions = (
        Admission.query.filter(Admission.hospital_id.in_(hospital_ids), Admission.status == "Active")
        .all()
    )
    by_ward = {}
    for a in active_admissions:
        by_ward.setdefault(a.ward_id, []).append(a)
    return render_template("inpatient/census.html", wards=wards, by_ward=by_ward)


# ---------------------------------------------------------------------------
# Doctor reviews — daily ward-round entries; any doctor with access can add
# one, so a stay naturally accumulates a review trail from whoever actually
# saw the patient that day.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/doctor-reviews", methods=["POST"])
@login_required
@permission_required("consultation.create")
def add_doctor_review(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    findings = (data.get("findings") or "").strip()
    assessment = (data.get("assessment") or "").strip()
    plan = (data.get("plan") or "").strip()
    if not findings and not assessment and not plan:
        return jsonify(success=False, error="Add at least a finding, assessment, or plan."), 400

    review = DoctorReview(
        admission_id=admission.id, doctor_id=current_user.id,
        findings=findings, assessment=assessment, plan=plan,
        procedure_done=data.get("procedure_done"),
    )
    db.session.add(review)
    log_action(current_user, "create", "DoctorReview", None, {"admission_id": admission.id})
    db.session.commit()
    return jsonify(success=True, id=review.id)


# ---------------------------------------------------------------------------
# Care plan / nursing cardex — the problems being actively managed and
# what nurses should do each shift. Items get marked Resolved rather than
# deleted, so the trail of what was managed over the stay stays intact.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/care-plans", methods=["POST"])
@login_required
@permission_required("triage.create")
def add_care_plan(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    problem = (data.get("problem") or "").strip()
    if not problem:
        return jsonify(success=False, error="Describe the problem being managed."), 400

    plan = CarePlan(
        admission_id=admission.id, problem=problem,
        goal=data.get("goal"), interventions=data.get("interventions"),
        created_by_id=current_user.id,
    )
    db.session.add(plan)
    db.session.commit()
    return jsonify(success=True, id=plan.id)


@inpatient_bp.route("/care-plans/<int:plan_id>/status", methods=["POST"])
@login_required
@permission_required("triage.create")
def update_care_plan_status(plan_id):
    plan = CarePlan.query.get_or_404(plan_id)
    if plan.admission.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    data = request.get_json(silent=True) or request.form
    status = data.get("status")
    if status not in CARE_PLAN_STATUSES:
        return jsonify(success=False, error="Invalid status."), 400
    plan.status = status
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Monitoring charts — intake/output, blood sugar, pain score, or any other
# repeated numeric chart a ward tracks over the stay.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/monitoring", methods=["POST"])
@login_required
@permission_required("triage.create")
def add_monitoring_entry(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    chart_type = data.get("chart_type")
    if chart_type not in MONITORING_CHART_TYPES:
        return jsonify(success=False, error="Invalid chart type."), 400
    value = _to_float(data.get("value"))
    if value is None:
        return jsonify(success=False, error="Enter a numeric reading."), 400

    entry = MonitoringEntry(
        admission_id=admission.id, chart_type=chart_type, value=value,
        route_or_context=data.get("route_or_context"), notes=data.get("notes"),
        recorded_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(success=True, id=entry.id)


# ---------------------------------------------------------------------------
# Insurance authorization — billing records what the insurer has actually
# pre-authorized for this stay; charges accumulate against it live as
# lab/pharmacy/radiology/bed-day items are added elsewhere in the app.
# ---------------------------------------------------------------------------

@inpatient_bp.route("/admissions/<int:admission_id>/insurance-authorization", methods=["POST"])
@login_required
@permission_required("billing.manage")
def set_insurance_authorization(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    amount = _to_float(data.get("amount"))
    if amount is not None and amount < 0:
        return jsonify(success=False, error="Enter a valid amount."), 400
    admission.insurance_authorized_amount = amount
    admission.refresh_insurance_limit_flag()
    log_action(current_user, "update", "Admission", admission.id, {"insurance_authorized_amount": amount})
    db.session.commit()
    return jsonify(success=True, insurance_limit_reached=admission.insurance_limit_reached,
                    running_total=admission.running_bill_total())


# ---------------------------------------------------------------------------
# Discharge summary — autogenerated from everything already in the system
# (admission details, admitting + additional diagnoses, doctor reviews,
# procedures, labs/radiology done during the stay). Only discharge
# medications and discharge advice need to be typed by the doctor.
# ---------------------------------------------------------------------------

def _build_discharge_narrative(admission):
    patient = admission.patient
    lines = []
    lines.append(f"{patient.full_name} ({patient.gender or '—'}, {patient.age if patient.age is not None else '—'} yrs) "
                  f"was admitted to {admission.ward.name} on {admission.admission_date.strftime('%d %b %Y')}.")

    consultation = admission.admitting_consultation
    if consultation and consultation.all_diagnoses:
        dx = ", ".join(f"{d.description} ({d.code})" for d in consultation.all_diagnoses)
        lines.append(f"Admitting diagnosis: {dx}.")
    if consultation and consultation.chief_complaint:
        lines.append(f"Presenting complaint: {consultation.chief_complaint}.")

    if admission.transfers:
        moves = "; ".join(
            f"{t.from_ward.name if t.from_ward else 'admission'} -> {t.to_ward.name} on {t.transferred_at.strftime('%d %b')}"
            for t in reversed(admission.transfers)
        )
        lines.append(f"Ward movement during stay: {moves}.")

    if admission.doctor_reviews:
        lines.append("")
        lines.append("Hospital course (ward reviews):")
        for review in reversed(admission.doctor_reviews):  # oldest first
            date_str = review.created_at.strftime("%d %b %Y")
            by = review.doctor.full_name if review.doctor else ""
            entry = f"- {date_str} ({by}): {review.assessment or review.findings or ''}"
            if review.procedure_done:
                entry += f" Procedure: {review.procedure_done}."
            lines.append(entry)

    visit = admission.visit
    if visit:
        resulted_labs = [o for o in visit.lab_orders if o.status == "Result Ready"]
        if resulted_labs:
            lines.append("")
            lines.append("Investigations: " + "; ".join(f"{o.lab_test.name}: {o.result_value or 'see result'}" for o in resulted_labs) + ".")
        reported_rad = [o for o in visit.radiology_orders if o.status == "Report Ready"]
        if reported_rad:
            lines.append("Imaging: " + "; ".join(f"{o.radiology_test.name}: {o.findings or 'see report'}" for o in reported_rad) + ".")

    if admission.care_plans:
        problems = ", ".join(cp.problem for cp in admission.care_plans)
        lines.append(f"Problems managed on the nursing care plan: {problems}.")

    done_procedures = [p for p in admission.procedure_orders if p.status == "Done"]
    if done_procedures:
        proc_list = ", ".join(p.name for p in reversed(done_procedures))
        lines.append(f"Procedures/services performed during the stay: {proc_list}.")

    end = admission.actual_discharge_date or datetime.datetime.utcnow()
    lines.append("")
    lines.append(f"Discharged from {admission.ward.name} on {end.strftime('%d %b %Y')} "
                 f"after a stay of {admission.length_of_stay_days} day(s).")
    return "\n".join(lines)


@inpatient_bp.route("/admissions/<int:admission_id>/discharge-summary", methods=["POST"])
@login_required
@permission_required("consultation.create")
def generate_discharge_summary(admission_id):
    admission = _admission_or_403(admission_id)
    data = request.get_json(silent=True) or request.form
    discharge_medications = (data.get("discharge_medications") or "").strip()
    discharge_advice = (data.get("discharge_advice") or "").strip()

    narrative = _build_discharge_narrative(admission)
    recommendation_parts = []
    if discharge_medications:
        recommendation_parts.append(f"Discharge medications: {discharge_medications}")
    if discharge_advice:
        recommendation_parts.append(f"Advice: {discharge_advice}")
    recommendation = "\n".join(recommendation_parts)

    visit = admission.visit
    doc = admission.discharge_summary_document
    if not doc:
        hospital = visit.hospital
        year = datetime.date.today().year
        prefix = DOCUMENT_TYPE_PREFIXES.get("Discharge Summary", "DOC")
        count = MedicalDocument.query.filter_by(hospital_id=hospital.id, document_type="Discharge Summary").filter(
            MedicalDocument.document_number.like(f"{prefix}-{hospital.code}-{year}-%")
        ).count()
        doc = MedicalDocument(
            hospital_id=hospital.id, visit_id=visit.id, patient_id=admission.patient_id,
            document_type="Discharge Summary",
            document_number=f"{prefix}-{hospital.code}-{year}-{count + 1:04d}",
            issued_by_id=current_user.id,
        )
        db.session.add(doc)

    consultation = admission.admitting_consultation
    doc.diagnosis_code_id = consultation.diagnosis_code_id if consultation else None
    doc.body_text = narrative
    doc.recommendation = recommendation
    doc.follow_up_date = consultation.follow_up_date if consultation else None
    db.session.flush()
    admission.discharge_summary_document_id = doc.id

    log_action(current_user, "create", "MedicalDocument", doc.id, {"type": "Discharge Summary", "admission_id": admission.id})
    db.session.commit()
    return jsonify(success=True, document_id=doc.id)
