import datetime
import json

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required, any_permission_required
from app.models import Patient, Drug, DiagnosisCode, Visit, log_action
from app.security.access import load_patient, json_error, ok
from app.security import versioning
from app.records import service as svc, mch as mchlib, growth as growthlib
from app.records.models import (PatientProblem, PatientAllergy, PatientMedication, FamilyHistory, GrowthMeasurement,
                                AlliedHealthReferral, AlliedHealthSession, MchEncounter, ImmunizationRecord,
                                PROBLEM_STATUSES, ALLERGEN_TYPES, ALLERGY_SEVERITIES, ALLERGY_STATUSES, ALLERGY_STATUS_OPTIONS,
                                MED_STATUSES, FAMILY_RELATIONS, ALLIED_DISCIPLINES, ALLIED_STATUSES, MCH_TYPES)

records_bp = Blueprint("records", __name__, template_folder="../templates/records")


def _data():
    return request.get_json(silent=True) or request.form


def _date(v):
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ValueError("Enter dates as YYYY-MM-DD.")


def _s(v, n):
    v = (v or "").strip() if isinstance(v, str) else v
    return (v[:n] if isinstance(v, str) else v) or None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("Enter numbers only.")


def _child(model, obj_id, write=True):
    obj = db.session.get(model, obj_id) or abort(404)
    patient = load_patient(obj.patient_id, write=write)
    return obj, patient


# ---------------------------------------------------------------------------
# The clinical record page
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/record")
@login_required
@permission_required("patient.view")
def record_page(patient_id):
    patient = load_patient(patient_id, section="clinical-record")
    svc.import_legacy_allergies(patient, current_user.id)
    db.session.commit()
    dob = patient.date_of_birth
    from app.records.mch import due_vaccines
    given = [i.vaccine for i in patient.immunizations]
    due = due_vaccines(dob, given) if dob and patient.age is not None and patient.age < 5 else []
    can_edit = current_user.has_permission("record.edit") and patient.hospital_id in current_user.accessible_hospital_ids()
    return render_template(
        "records/record.html", patient=patient, can_edit=can_edit,
        can_refer=current_user.has_permission("allied.order") and can_edit,
        can_allied=current_user.has_permission("allied.manage") and can_edit,
        problems=sorted(patient.problems, key=lambda p: (p.status != "Active", -(p.id or 0))),
        allergies=sorted(patient.allergy_list, key=lambda a: (a.status != "Active", a.id)),
        medications=sorted(patient.medication_list, key=lambda m: (m.effective_status != "Active", -m.id)),
        family=patient.family_history, growth=patient.growth, referrals=patient.allied_referrals,
        mch=[(e, mchlib.loads(e.data)) for e in sorted(patient.mch_encounters, key=lambda e: e.encounter_date, reverse=True)],
        immunizations=patient.immunizations, due_vaccines=due, mch_schemas=mchlib.MCH_SCHEMAS,
        reference_loaded=svc.reference_loaded(), growth_indicators=growthlib.INDICATORS,
        classify=growthlib.classify, muac_class=growthlib.muac_class,
        problem_statuses=PROBLEM_STATUSES, allergen_types=ALLERGEN_TYPES, severities=ALLERGY_SEVERITIES,
        allergy_statuses=ALLERGY_STATUSES, allergy_status_options=ALLERGY_STATUS_OPTIONS, med_statuses=MED_STATUSES,
        relations=FAMILY_RELATIONS, disciplines=ALLIED_DISCIPLINES, referral_statuses=ALLIED_STATUSES, mch_types=MCH_TYPES,
        history=versioning.patient_history(patient.id, 100) if current_user.has_permission("record.edit") or current_user.has_permission("audit.view") else [],
        today=datetime.date.today().isoformat(),
    )


@records_bp.route("/patients/<int:patient_id>/record/history")
@login_required
@permission_required("patient.view")
def record_history(patient_id):
    patient = load_patient(patient_id, section="record-history")
    entity = request.args.get("entity")
    eid = request.args.get("id", type=int)
    rows = versioning.history_for(entity, eid) if entity and eid else versioning.patient_history(patient.id)
    rows = [r for r in rows if r.patient_id in (None, patient.id)]
    return jsonify(versions=[{
        "entity": r.entity, "entity_id": r.entity_id, "version": r.version_no, "action": r.action,
        "changes": json.loads(r.changes) if r.changes and r.action != "delete" else None,
        "amendment": r.is_amendment, "reason": r.amendment_reason,
        "by": r.changed_by.full_name if r.changed_by else None, "at": r.changed_at.isoformat()} for r in rows])


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/problems", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_problem(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    try:
        code = db.session.get(DiagnosisCode, int(d["diagnosis_code_id"])) if d.get("diagnosis_code_id") else None
        desc = _s(d.get("description"), 255) or (code.description if code else None)
        if not desc:
            return json_error("Describe the problem or pick a diagnosis.")
        p = PatientProblem(patient_id=patient.id, hospital_id=patient.hospital_id, diagnosis_code_id=code.id if code else None,
                           description=desc, status="Active", is_chronic=str(d.get("is_chronic", "")).lower() in ("1", "true", "on"),
                           onset_date=_date(d.get("onset_date")), notes=_s(d.get("notes"), 500), recorded_by_id=current_user.id)
    except (ValueError, TypeError) as e:
        return json_error(str(e))
    db.session.add(p)
    db.session.flush()
    log_action(current_user, "create", "PatientProblem", p.id, {"description": desc}, patient_id=patient.id)
    db.session.commit()
    return ok(id=p.id)


@records_bp.route("/problems/<int:problem_id>", methods=["POST"])
@login_required
@permission_required("record.edit")
def update_problem(problem_id):
    p, patient = _child(PatientProblem, problem_id)
    d = _data()
    try:
        status = d.get("status") or p.status
        if status not in PROBLEM_STATUSES:
            return json_error("Invalid status.")
        reason, err = versioning.require_amendment_reason(d, is_final=status != p.status and p.status in ("Resolved", "Ruled Out"))
        if err:
            return json_error(err)
        p.status = status
        if "notes" in d:
            p.notes = _s(d.get("notes"), 500)
        if "description" in d and d.get("description"):
            p.description = _s(d["description"], 255)
        if "is_chronic" in d:
            p.is_chronic = str(d["is_chronic"]).lower() in ("1", "true", "on")
        p.resolved_date = _date(d.get("resolved_date")) or (datetime.date.today() if status == "Resolved" and not p.resolved_date else p.resolved_date)
        if status in ("Active", "Inactive"):
            p.resolved_date = None
        p.updated_by_id = current_user.id
    except ValueError as e:
        return json_error(str(e))
    log_action(current_user, "update", "PatientProblem", p.id, {"status": status}, patient_id=patient.id)
    db.session.commit()
    return ok()


# ---------------------------------------------------------------------------
# Allergies
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/allergies", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_allergy(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    name = _s(d.get("allergen_name"), 150)
    drug = None
    if d.get("drug_id"):
        drug = Drug.query.filter_by(id=int(d["drug_id"]), organization_id=current_user.organization_id).first()
        if not drug:
            return json_error("Unknown drug.")
        name = name or drug.name
    if not name:
        return json_error("Enter the allergen.")
    if d.get("allergen_type", "Drug") not in ALLERGEN_TYPES or d.get("severity", "Moderate") not in ALLERGY_SEVERITIES:
        return json_error("Invalid type or severity.")
    try:
        a = PatientAllergy(patient_id=patient.id, hospital_id=patient.hospital_id, allergen_type=d.get("allergen_type", "Drug"),
                           allergen_name=name, drug_id=drug.id if drug else None, hpt_code=drug.hpt_code if drug else _s(d.get("hpt_code"), 50),
                           snomed_ct_code=_s(d.get("snomed_ct_code"), 20), reaction=_s(d.get("reaction"), 200),
                           severity=d.get("severity", "Moderate"), onset_date=_date(d.get("onset_date")),
                           notes=_s(d.get("notes"), 300), source=_s(d.get("source"), 20) or "Clinician", recorded_by_id=current_user.id)
    except ValueError as e:
        return json_error(str(e))
    db.session.add(a)
    db.session.flush()
    patient.allergy_status, patient.allergy_status_recorded_at = "Has allergies", datetime.datetime.utcnow()
    svc.sync_legacy_allergy_text(patient)
    log_action(current_user, "create", "PatientAllergy", a.id, {"allergen": name}, patient_id=patient.id)
    db.session.commit()
    return ok(id=a.id)


@records_bp.route("/allergies/<int:allergy_id>", methods=["POST"])
@login_required
@permission_required("record.edit")
def update_allergy(allergy_id):
    a, patient = _child(PatientAllergy, allergy_id)
    d = _data()
    status = d.get("status") or a.status
    if status not in ALLERGY_STATUSES or d.get("severity", a.severity) not in ALLERGY_SEVERITIES:
        return json_error("Invalid status or severity.")
    reason, err = versioning.require_amendment_reason(d, is_final=status in ("Inactive", "Resolved", "Entered in error") and status != a.status)
    if err:
        return json_error("Removing or deactivating an allergy needs a reason.")
    a.status, a.severity = status, d.get("severity", a.severity)
    for f, n in (("reaction", 200), ("notes", 300)):
        if f in d:
            setattr(a, f, _s(d.get(f), n))
    a.updated_by_id = current_user.id
    db.session.flush()
    svc.sync_legacy_allergy_text(patient)
    log_action(current_user, "update", "PatientAllergy", a.id, {"status": status}, patient_id=patient.id)
    db.session.commit()
    return ok()


@records_bp.route("/patients/<int:patient_id>/allergy-status", methods=["POST"])
@login_required
@permission_required("record.edit")
def set_allergy_status(patient_id):
    patient = load_patient(patient_id, write=True)
    status = _data().get("status")
    if status not in ALLERGY_STATUS_OPTIONS:
        return json_error("Invalid status.")
    if status == "No known allergies" and any(a.status == "Active" for a in patient.allergy_list):
        return json_error("This patient has active allergies on the list. Deactivate them first.")
    patient.allergy_status, patient.allergy_status_recorded_at = status, datetime.datetime.utcnow()
    log_action(current_user, "update", "Patient", patient.id, {"allergy_status": status}, patient_id=patient.id)
    db.session.commit()
    return ok()


# ---------------------------------------------------------------------------
# Medication list
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/medications", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_medication(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    drug = None
    if d.get("drug_id"):
        drug = Drug.query.filter_by(id=int(d["drug_id"]), organization_id=current_user.organization_id).first()
    name = _s(d.get("medication_name"), 200) or (drug.name if drug else None)
    if not name:
        return json_error("Enter the medication.")
    try:
        m = PatientMedication(patient_id=patient.id, hospital_id=patient.hospital_id, drug_id=drug.id if drug else None,
                              medication_name=name, hpt_code=drug.hpt_code if drug else None, dosage=_s(d.get("dosage"), 60),
                              frequency=_s(d.get("frequency"), 60), route=_s(d.get("route"), 30), indication=_s(d.get("indication"), 200),
                              start_date=_date(d.get("start_date")), end_date=_date(d.get("end_date")), status="Active",
                              source=_s(d.get("source"), 30) or "Patient reported", recorded_by_id=current_user.id)
    except ValueError as e:
        return json_error(str(e))
    db.session.add(m)
    db.session.flush()
    log_action(current_user, "create", "PatientMedication", m.id, {"name": name}, patient_id=patient.id)
    db.session.commit()
    return ok(id=m.id)


@records_bp.route("/medications/<int:med_id>", methods=["POST"])
@login_required
@permission_required("record.edit")
def update_medication(med_id):
    m, patient = _child(PatientMedication, med_id)
    d = _data()
    status = d.get("status") or m.status
    if status not in MED_STATUSES:
        return json_error("Invalid status.")
    if status in ("Stopped", "Completed") and status != m.status:
        m.end_date = m.end_date or datetime.date.today()
        m.stop_reason = _s(d.get("stop_reason"), 200)
        if status == "Stopped" and not m.stop_reason:
            return json_error("Say why the medication was stopped.")
    m.status = status
    log_action(current_user, "update", "PatientMedication", m.id, {"status": status}, patient_id=patient.id)
    db.session.commit()
    return ok()


# ---------------------------------------------------------------------------
# Family history
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/family-history", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_family_history(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    if d.get("relationship_to_patient") not in FAMILY_RELATIONS or not _s(d.get("condition"), 200):
        return json_error("Choose the relative and enter the condition.")
    try:
        age = int(d["age_at_onset"]) if d.get("age_at_onset") else None
    except ValueError:
        return json_error("Age at onset must be a number.")
    f = FamilyHistory(patient_id=patient.id, hospital_id=patient.hospital_id, relationship_to_patient=d["relationship_to_patient"],
                      condition=_s(d["condition"], 200), diagnosis_code_id=int(d["diagnosis_code_id"]) if d.get("diagnosis_code_id") else None,
                      age_at_onset=age, deceased=str(d.get("deceased", "")).lower() in ("1", "true", "on"),
                      notes=_s(d.get("notes"), 300), recorded_by_id=current_user.id)
    db.session.add(f)
    db.session.flush()
    log_action(current_user, "create", "FamilyHistory", f.id, patient_id=patient.id)
    db.session.commit()
    return ok(id=f.id)


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/growth", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_growth(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    try:
        w, h, hc, mu = (_num(d.get(k)) for k in ("weight_kg", "height_cm", "head_circumference_cm", "muac_cm"))
        when = _date(d.get("measured_on")) or datetime.date.today()
    except ValueError as e:
        return json_error(str(e))
    if not any((w, h, hc, mu)):
        return json_error("Enter at least one measurement.")
    if (w and not 0.3 <= w <= 400) or (h and not 20 <= h <= 260) or (hc and not 20 <= hc <= 80) or (mu and not 4 <= mu <= 60):
        return json_error("A measurement is outside a believable range. Check the units (kg, cm).")
    if patient.date_of_birth and when < patient.date_of_birth:
        return json_error("Measurement date is before the date of birth.")
    g = svc.record_growth(patient, w, h, hc, mu, measured_on=when, source="Manual", user_id=current_user.id)
    db.session.flush()
    log_action(current_user, "create", "GrowthMeasurement", g.id, patient_id=patient.id)
    db.session.commit()
    return ok(id=g.id)


@records_bp.route("/patients/<int:patient_id>/growth.json")
@login_required
@permission_required("patient.view")
def growth_json(patient_id):
    patient = load_patient(patient_id, section="growth")
    rows = GrowthMeasurement.query.filter_by(patient_id=patient.id).order_by(GrowthMeasurement.measured_on).all()
    return jsonify(reference_loaded=svc.reference_loaded(), points=[{
        "date": g.measured_on.isoformat(), "age_days": g.age_days, "weight_kg": g.weight_kg, "height_cm": g.height_cm,
        "bmi": g.bmi, "head_cm": g.head_circumference_cm, "muac_cm": g.muac_cm, "waz": g.waz, "haz": g.haz, "baz": g.baz,
        "whz": g.whz, "hcz": g.hcz} for g in rows])


# ---------------------------------------------------------------------------
# Allied health: physiotherapy, OT, nutrition, social work, counselling
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/allied-referrals", methods=["POST"])
@login_required
@permission_required("allied.order")
def add_referral(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    if d.get("discipline") not in ALLIED_DISCIPLINES or not _s(d.get("reason"), 500):
        return json_error("Choose a service and give the reason for referral.")
    visit = None
    if d.get("visit_id"):
        visit = Visit.query.filter_by(id=int(d["visit_id"]), patient_id=patient.id).first()
    else:
        visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.created_at.desc()).first()
    try:
        fee = _num(d.get("fee")) or 0
    except ValueError as e:
        return json_error(str(e))
    r = AlliedHealthReferral(patient_id=patient.id, hospital_id=patient.hospital_id, visit_id=visit.id if visit else None,
                             discipline=d["discipline"], reason=_s(d["reason"], 500), priority="Urgent" if d.get("priority") == "Urgent" else "Routine",
                             goals=_s(d.get("goals"), 2000), fee=fee,
                             is_confidential=d["discipline"] in ("Counselling", "Social Work"), referred_by_id=current_user.id)
    db.session.add(r)
    db.session.flush()
    log_action(current_user, "create", "AlliedHealthReferral", r.id, {"discipline": r.discipline}, patient_id=patient.id)
    db.session.commit()
    return ok(id=r.id)


def _can_see_referral(r):
    """Counselling/social-work content is confidential: only the referrer and allied-health staff."""
    if not r.is_confidential:
        return True
    return current_user.has_permission("allied.manage") or r.referred_by_id == current_user.id


@records_bp.route("/allied-referrals/<int:ref_id>/status", methods=["POST"])
@login_required
@any_permission_required("allied.manage", "allied.order")
def referral_status(ref_id):
    r, patient = _child(AlliedHealthReferral, ref_id)
    if not _can_see_referral(r):
        abort(403)
    status = _data().get("status")
    if status not in ALLIED_STATUSES:
        return json_error("Invalid status.")
    if status == "Cancelled" and not (current_user.id == r.referred_by_id or current_user.has_permission("allied.manage")):
        abort(403)
    r.status = status
    if status in ("Accepted", "In Progress") and not r.assigned_to_id and current_user.has_permission("allied.manage"):
        r.assigned_to_id = current_user.id
    if status == "Completed":
        r.completed_at = datetime.datetime.utcnow()
    log_action(current_user, "update", "AlliedHealthReferral", r.id, {"status": status}, patient_id=patient.id)
    db.session.commit()
    return ok()


@records_bp.route("/allied-referrals/<int:ref_id>/sessions", methods=["POST"])
@login_required
@permission_required("allied.manage")
def add_session(ref_id):
    r, patient = _child(AlliedHealthReferral, ref_id)
    d = _data()
    if r.status in ("Completed", "Cancelled"):
        return json_error("This referral is closed.")
    try:
        fee = _num(d.get("fee"))
        when = _date(d.get("session_date")) or datetime.date.today()
    except ValueError as e:
        return json_error(str(e))
    if not any(_s(d.get(k), 4000) for k in ("subjective", "objective", "assessment", "plan")):
        return json_error("Write at least one section of the note.")
    s = AlliedHealthSession(referral_id=r.id, patient_id=patient.id, session_date=when, subjective=_s(d.get("subjective"), 4000),
                            objective=_s(d.get("objective"), 4000), assessment=_s(d.get("assessment"), 4000),
                            plan=_s(d.get("plan"), 4000), fee=fee if fee is not None else (r.fee or 0), provider_id=current_user.id)
    db.session.add(s)
    if r.status == "Referred":
        r.status = "In Progress"
    db.session.flush()
    log_action(current_user, "create", "AlliedHealthSession", s.id, {"referral": r.id}, patient_id=patient.id)
    db.session.commit()
    return ok(id=s.id)


@records_bp.route("/allied/worklist")
@login_required
@permission_required("allied.manage")
def allied_worklist():
    ids = current_user.accessible_hospital_ids()
    rows = (AlliedHealthReferral.query.filter(AlliedHealthReferral.hospital_id.in_(ids),
                                              AlliedHealthReferral.status.in_(["Referred", "Accepted", "In Progress"]))
            .order_by(AlliedHealthReferral.priority.desc(), AlliedHealthReferral.created_at).all())
    return render_template("records/allied_worklist.html", referrals=rows)


# ---------------------------------------------------------------------------
# MCH + immunization
# ---------------------------------------------------------------------------
@records_bp.route("/patients/<int:patient_id>/mch", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_mch(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    etype = d.get("encounter_type")
    if etype in ("ANC", "PNC", "Delivery", "Family Planning") and patient.gender == "Male":
        return json_error(f"{etype} does not apply to a male patient.")
    try:
        clean = mchlib.validate(etype, d)
        when, nxt = _date(d.get("encounter_date")) or datetime.date.today(), _date(d.get("next_visit_date"))
    except (mchlib.MchValidationError, ValueError) as e:
        return json_error(str(e))
    visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.created_at.desc()).first()
    e = MchEncounter(patient_id=patient.id, hospital_id=patient.hospital_id, visit_id=visit.id if visit else None,
                     encounter_type=etype, encounter_date=when, data=mchlib.dumps(clean), next_visit_date=nxt,
                     notes=_s(d.get("notes"), 500), recorded_by_id=current_user.id)
    db.session.add(e)
    db.session.flush()
    log_action(current_user, "create", "MchEncounter", e.id, {"type": etype}, patient_id=patient.id)
    db.session.commit()
    return ok(id=e.id)


@records_bp.route("/patients/<int:patient_id>/immunizations", methods=["POST"])
@login_required
@permission_required("record.edit")
def add_immunization(patient_id):
    patient = load_patient(patient_id, write=True)
    d = _data()
    try:
        given = _date(d.get("date_given"))
        nxt = _date(d.get("next_due_date"))
        dose = int(d["dose_number"]) if d.get("dose_number") else None
    except ValueError:
        return json_error("Check the dates (YYYY-MM-DD) and dose number.")
    if not _s(d.get("vaccine"), 60) or not given:
        return json_error("Vaccine and date given are required.")
    if given > datetime.date.today():
        return json_error("Date given cannot be in the future.")
    i = ImmunizationRecord(patient_id=patient.id, hospital_id=patient.hospital_id, vaccine=_s(d["vaccine"], 60), dose_number=dose,
                           date_given=given, batch_number=_s(d.get("batch_number"), 40), site=_s(d.get("site"), 30), next_due_date=nxt,
                           given_elsewhere=str(d.get("given_elsewhere", "")).lower() in ("1", "true", "on"),
                           notes=_s(d.get("notes"), 200), recorded_by_id=current_user.id)
    db.session.add(i)
    db.session.flush()
    log_action(current_user, "create", "ImmunizationRecord", i.id, {"vaccine": i.vaccine}, patient_id=patient.id)
    db.session.commit()
    return ok(id=i.id)
