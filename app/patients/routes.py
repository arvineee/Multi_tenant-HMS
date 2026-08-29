import datetime

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import Patient, Visit, InsuranceScheme, Ward, Drug, log_action
from app.level_policy import hospital_allows_inpatient

patients_bp = Blueprint("patients", __name__, template_folder="../templates/patients")


def _next_patient_number(hospital):
    """Format: <HOSPITAL_CODE>-<YEAR>-<sequence>, e.g. NRB-01-2026-00001"""
    year = datetime.date.today().year
    count = Patient.query.filter_by(hospital_id=hospital.id).filter(
        Patient.patient_number.like(f"{hospital.code}-{year}-%")
    ).count()
    return f"{hospital.code}-{year}-{count + 1:05d}"


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@patients_bp.route("/patients", methods=["GET"])
@login_required
@permission_required("patient.view")
def list_patients():
    hospital = current_user.hospital
    q = (request.args.get("q") or "").strip()
    show_archived = request.args.get("archived") == "1"

    query = Patient.query.filter_by(hospital_id=hospital.id) if hospital else Patient.query.filter(False)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Patient.first_name.ilike(like),
                Patient.last_name.ilike(like),
                Patient.patient_number.ilike(like),
                Patient.phone.ilike(like),
                Patient.national_id.ilike(like),
            )
        )
    # Discharged/completed patients move out of the default (active) list
    # into the archive so front-desk and clinical lists aren't cluttered
    # with people who aren't currently being seen. A patient reappears
    # here automatically the moment they check in again — no manual
    # un-archiving needed. Filtering happens in Python (via is_archived)
    # since it depends on each patient's latest visit status, not a
    # stored column; a generous candidate window keeps this cheap for a
    # single clinic's patient volume.
    candidates = query.options(db.joinedload(Patient.visits)).order_by(Patient.created_at.desc()).limit(500).all()
    filtered = [p for p in candidates if p.is_archived == show_archived]
    patients = filtered[:100]

    insurance_schemes = InsuranceScheme.query.filter_by(
        organization_id=current_user.organization_id, is_active=True
    ).order_by(InsuranceScheme.name).all()
    return render_template(
        "patients/list.html", patients=patients, query=q, insurance_schemes=insurance_schemes,
        allows_inpatient=hospital_allows_inpatient(hospital), show_archived=show_archived,
    )


@patients_bp.route("/patients", methods=["POST"])
@login_required
@permission_required("patient.register")
def register_patient():
    hospital = current_user.hospital
    if not hospital:
        return jsonify(success=False, error="Your account isn't tied to a hospital."), 400

    data = request.get_json(silent=True) or request.form
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()

    if not first_name or not last_name:
        return jsonify(success=False, error="First and last name are required."), 400

    visit_type = data.get("visit_type", "Outpatient")
    if visit_type == "Inpatient" and not hospital_allows_inpatient(hospital):
        return jsonify(success=False, error=f"{hospital.level} facilities don't offer inpatient admission."), 400

    date_of_birth = _parse_date(data.get("date_of_birth"))
    estimated_age_years = None
    estimated_age_recorded_on = None
    if not date_of_birth:
        raw_age = (data.get("estimated_age_years") or "").strip() if isinstance(data.get("estimated_age_years"), str) else data.get("estimated_age_years")
        if raw_age not in (None, ""):
            try:
                estimated_age_years = int(raw_age)
            except (TypeError, ValueError):
                return jsonify(success=False, error="Age must be a whole number."), 400
            if estimated_age_years < 0 or estimated_age_years > 130:
                return jsonify(success=False, error="Enter a valid age."), 400
            estimated_age_recorded_on = datetime.date.today()

    if not date_of_birth and estimated_age_years is None:
        return jsonify(success=False, error="Enter either a date of birth or an approximate age."), 400

    patient = Patient(
        hospital_id=hospital.id,
        patient_number=_next_patient_number(hospital),
        first_name=first_name,
        last_name=last_name,
        gender=data.get("gender"),
        date_of_birth=date_of_birth,
        estimated_age_years=estimated_age_years,
        estimated_age_recorded_on=estimated_age_recorded_on,
        national_id=data.get("national_id"),
        phone=data.get("phone"),
        address=data.get("address"),
        blood_group=data.get("blood_group"),
        next_of_kin_name=data.get("next_of_kin_name"),
        next_of_kin_phone=data.get("next_of_kin_phone"),
        next_of_kin_relationship=data.get("next_of_kin_relationship"),
        allergies=data.get("allergies"),
        chronic_conditions=data.get("chronic_conditions"),
        insurance_scheme_id=data.get("insurance_scheme_id") or None,
        insurance_member_number=data.get("insurance_member_number"),
        created_by_id=current_user.id,
    )
    db.session.add(patient)
    db.session.flush()

    # registering a patient always opens their first visit so they land in
    # the day's queue immediately
    visit = Visit(
        hospital_id=hospital.id,
        patient_id=patient.id,
        visit_type=visit_type,
        reason=data.get("reason"),
        checked_in_by_id=current_user.id,
    )
    db.session.add(visit)
    log_action(current_user, "create", "Patient", patient.id, {"patient_number": patient.patient_number})

    db.session.commit()

    return jsonify(success=True, id=patient.id, patient_number=patient.patient_number)


@patients_bp.route("/patients/<int:patient_id>", methods=["GET"])
@login_required
@permission_required("patient.view")
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if patient.hospital_id not in current_user.accessible_hospital_ids():
        from flask import abort
        abort(403)
    wards = Ward.query.filter_by(hospital_id=patient.hospital_id, is_active=True).order_by(Ward.name).all()

    # compiled diagnosis history across every past visit, most recent first
    past_diagnoses = [
        v.consultation for v in patient.visits
        if v.consultation and v.consultation.diagnosis_code
    ]
    # recent vitals trend (most recent triage records first)
    vitals_trend = [v.triage for v in patient.visits if v.triage][:5]

    triage_relief_drugs = Drug.query.filter_by(
        organization_id=current_user.organization_id, is_active=True, is_triage_relief=True
    ).order_by(Drug.name).all()

    return render_template(
        "patients/detail.html", patient=patient,
        wards=wards, past_diagnoses=past_diagnoses, vitals_trend=vitals_trend,
        allows_inpatient=hospital_allows_inpatient(patient.hospital),
        triage_relief_drugs=triage_relief_drugs,
    )


@patients_bp.route("/patients/<int:patient_id>/visits", methods=["POST"])
@login_required
@permission_required("patient.register")
def check_in_visit(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if patient.hospital_id not in current_user.accessible_hospital_ids():
        return jsonify(success=False, error="Not allowed."), 403

    data = request.get_json(silent=True) or request.form
    visit_type = data.get("visit_type", "Outpatient")
    if visit_type == "Inpatient" and not hospital_allows_inpatient(patient.hospital):
        return jsonify(success=False, error=f"{patient.hospital.level} facilities don't offer inpatient admission."), 400

    visit = Visit(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        visit_type=visit_type,
        reason=data.get("reason"),
        checked_in_by_id=current_user.id,
    )
    db.session.add(visit)
    db.session.flush()  # so visit.id is populated before we log it
    log_action(current_user, "create", "Visit", visit.id, {"patient_id": patient.id})
    db.session.commit()
    return jsonify(success=True, id=visit.id)


@patients_bp.route("/visits/<int:visit_id>/status", methods=["POST"])
@login_required
@permission_required("patient.view")
def update_visit_status(visit_id):
    visit = Visit.query.get_or_404(visit_id)
    if visit.hospital_id not in current_user.accessible_hospital_ids():
        return jsonify(success=False, error="Not allowed."), 403

    data = request.get_json(silent=True) or request.form
    new_status = data.get("status")
    from app.models import VISIT_STATUSES
    if new_status not in VISIT_STATUSES:
        return jsonify(success=False, error="Invalid status."), 400

    visit.status = new_status
    if new_status in ("Completed", "Discharged"):
        visit.closed_at = datetime.datetime.utcnow()
    log_action(current_user, "update", "Visit", visit.id, {"status": new_status})
    db.session.commit()
    return jsonify(success=True)
