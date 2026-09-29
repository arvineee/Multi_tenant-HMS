"""Builds the CDS context from the chart and records alerts/overrides."""
import datetime

from app.extensions import db
from app.models import Drug, Triage, LabOrder, log_action
from app.records.models import PatientAllergy, PatientMedication, PatientProblem
from app.records import service as rec
from app.cds import rules
from app.cds.models import CdsRuleSetting, CdsAlertLog


def age_months(patient):
    dob = patient.date_of_birth
    if dob:
        t = datetime.date.today()
        return (t.year - dob.year) * 12 + t.month - dob.month - (1 if t.day < dob.day else 0)
    return patient.age * 12 if patient.age is not None else None


def build_context(patient, visit=None):
    allergies = [dict(allergen_name=a.allergen_name, drug_id=a.drug_id, severity=a.severity, status=a.status,
                      reaction=a.reaction) for a in PatientAllergy.query.filter_by(patient_id=patient.id).all()]
    meds = []
    for m in PatientMedication.query.filter_by(patient_id=patient.id).all():
        if m.effective_status == "Active":
            meds.append(dict(drug_id=m.drug_id, name=m.medication_name, generic_name=m.drug.generic_name if m.drug else ""))
    problems = [dict(description=p.description, status=p.status, code=p.code_display, is_chronic=p.is_chronic)
                for p in PatientProblem.query.filter_by(patient_id=patient.id).all()]
    tri = None
    if visit is not None and visit.triage:
        tri = visit.triage
    else:
        tri = (Triage.query.filter_by(patient_id=patient.id).order_by(Triage.created_at.desc()).first())
    vitals = {}
    if tri:
        vitals = dict(temperature_c=tri.temperature_c, pulse_bpm=tri.pulse_bpm, bp_systolic=tri.bp_systolic,
                      bp_diastolic=tri.bp_diastolic, respiratory_rate=tri.respiratory_rate, spo2_percent=tri.spo2_percent)
    return rules.Context(age_months=age_months(patient), sex={"Male": "M", "Female": "F"}.get(patient.gender),
                         pregnant=rec.pregnancy_status(patient.id), allergies=allergies,
                         active_medications=meds, problems=problems, vitals=vitals)


def drug_info(d):
    return rules.DrugInfo(d.id, d.name, d.generic_name or "", d.atc_code or "", d.hpt_code or "",
                          bool(d.pregnancy_caution), d.min_age_months)


def enabled_fn(org_id):
    off = {s.rule_id for s in CdsRuleSetting.query.filter_by(organization_id=org_id, enabled=False).all()}
    return lambda rid: rid not in off


def evaluate(patient, visit, drug_ids, org_id):
    drugs = [drug_info(d) for d in Drug.query.filter(Drug.id.in_(drug_ids or [0]),
                                                     Drug.organization_id == org_id).all()] if drug_ids else []
    return rules.evaluate_all(build_context(patient, visit), drugs, enabled_fn(org_id))


def log_alerts(user, patient, visit, alerts, action="shown", reason=None):
    for a in alerts:
        db.session.add(CdsAlertLog(hospital_id=patient.hospital_id, patient_id=patient.id,
                                   visit_id=visit.id if visit else None, user_id=user.id, rule_id=a.rule_id,
                                   severity=a.severity, message=a.message[:500], action=action,
                                   override_reason=(reason or None) and reason[:300]))


def lab_alert_for(order):
    t = order.lab_test
    return rules.check_lab_result(t.name, order.result_value, t.ref_low, t.ref_high, t.critical_low, t.critical_high, t.unit or "")
