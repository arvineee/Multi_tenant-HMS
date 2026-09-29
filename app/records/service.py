"""DB-level helpers for the structured clinical record."""
import datetime
import re

from app.extensions import db
from app.models import Patient, Drug, log_action
from app.records.models import (PatientProblem, PatientAllergy, PatientMedication, GrowthMeasurement,
                                GrowthReference, MchEncounter)
from app.records import growth, mch


def sync_legacy_allergy_text(patient):
    """Keep Patient.allergies (free text used by older screens) and allergy_status
    consistent with the structured list."""
    active = [a for a in PatientAllergy.query.filter_by(patient_id=patient.id, status="Active").all()]
    patient.allergies = ", ".join(a.allergen_name for a in active)[:500] or None
    if active:
        patient.allergy_status = "Has allergies"
        patient.allergy_status_recorded_at = patient.allergy_status_recorded_at or datetime.datetime.utcnow()


def import_legacy_allergies(patient, user_id=None):
    """Split the old free-text allergy field into structured rows (once)."""
    if patient.allergy_list or not (patient.allergies or "").strip():
        return 0
    n = 0
    for part in re.split(r"[;,\n]+", patient.allergies):
        name = part.strip()
        if name:
            db.session.add(PatientAllergy(patient_id=patient.id, hospital_id=patient.hospital_id, allergen_name=name[:150],
                                          source="Legacy", recorded_by_id=user_id))
            n += 1
    if n:
        patient.allergy_status = "Has allergies"
    return n


def promote_diagnoses_to_problems(consultation, user_id):
    """Add the consultation's diagnoses to the patient's problem list (no duplicates)."""
    existing = {(p.diagnosis_code_id, p.description.lower()) for p in
                PatientProblem.query.filter_by(patient_id=consultation.patient_id).all()}
    added = 0
    for dx in consultation.all_diagnoses:
        key = (dx.id, dx.description.lower())
        if key in existing:
            continue
        db.session.add(PatientProblem(
            patient_id=consultation.patient_id, hospital_id=consultation.hospital_id, diagnosis_code_id=dx.id,
            description=dx.description[:255], status="Active", onset_date=datetime.date.today(),
            source="Consultation", source_consultation_id=consultation.id, recorded_by_id=user_id))
        added += 1
    return added


_DUR = re.compile(r"(\d+)\s*(day|days|d|week|weeks|wk|wks|month|months)", re.I)


def end_date_from_duration(duration, start=None):
    m = _DUR.search(duration or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * (7 if unit.startswith(("w",)) else 30 if unit.startswith("m") else 1)
    return (start or datetime.date.today()) + datetime.timedelta(days=days)


def add_medications_from_items(prescription, items):
    """Create medication-list rows for each prescribed item. `items` are PrescriptionItem objects (flushed)."""
    today = datetime.date.today()
    for it in items:
        d = it.drug
        db.session.add(PatientMedication(
            patient_id=prescription.patient_id, hospital_id=prescription.hospital_id, drug_id=d.id,
            medication_name=f"{d.name}"[:200], hpt_code=d.hpt_code, dosage=it.dosage, frequency=it.frequency,
            start_date=today, end_date=end_date_from_duration(it.duration, today), status="Active",
            source="Prescribed here", prescription_item_id=it.id, recorded_by_id=prescription.doctor_id))


def pregnancy_status(patient_id):
    rows = [(e.encounter_type, e.encounter_date, mch.loads(e.data))
            for e in MchEncounter.query.filter_by(patient_id=patient_id).all()]
    return mch.is_pregnant_from(rows)


def _ref_rows(indicator, sex):
    rows = (GrowthReference.query.filter_by(indicator=indicator, sex=sex).order_by(GrowthReference.x).all())
    return [(r.x, r.L, r.M, r.S) for r in rows]


def reference_loaded():
    return GrowthReference.query.first() is not None


def record_growth(patient, weight_kg=None, height_cm=None, head_cm=None, muac_cm=None, visit_id=None,
                  triage_id=None, measured_on=None, source="Manual", user_id=None):
    measured_on = measured_on or datetime.date.today()
    age_days = (measured_on - patient.date_of_birth).days if patient.date_of_birth else None
    bmi = round(weight_kg / ((height_cm / 100) ** 2), 1) if weight_kg and height_cm else None
    g = (GrowthMeasurement.query.filter_by(triage_id=triage_id).first() if triage_id else None) or GrowthMeasurement(
        patient_id=patient.id, hospital_id=patient.hospital_id, triage_id=triage_id)
    g.visit_id, g.measured_on, g.age_days, g.source, g.recorded_by_id = visit_id, measured_on, age_days, source, user_id
    g.weight_kg, g.height_cm, g.head_circumference_cm, g.muac_cm, g.bmi = weight_kg, height_cm, head_cm, muac_cm, bmi
    sex = {"Male": "M", "Female": "F"}.get(patient.gender)
    if age_days is not None and age_days <= 19 * 365 and reference_loaded():
        cache = {}
        def lookup(ind, s):
            if (ind, s) not in cache:
                cache[(ind, s)] = _ref_rows(ind, s)
            return cache[(ind, s)]
        growth.compute_for(g, sex, lookup)
    db.session.add(g)
    return g


def growth_from_triage(triage, patient, user_id=None):
    if not (triage.weight_kg or triage.height_cm or triage.head_circumference_cm or triage.muac_cm):
        return None
    return record_growth(patient, triage.weight_kg, triage.height_cm, triage.head_circumference_cm, triage.muac_cm,
                         visit_id=triage.visit_id, triage_id=triage.id, source="Triage", user_id=user_id)
