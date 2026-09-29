"""Assembles the plain-dict clinical summary used for the human-readable view
and the FHIR bundle (one source of truth for both)."""
import datetime

from app.models import Visit, Triage, LabOrder, Prescription, CarePlan, Admission
from app.records.models import (PatientProblem, PatientAllergy, PatientMedication, ImmunizationRecord)


def _d(x):
    return x.isoformat() if x else None


def gather_summary(patient, max_visits=20):
    h = patient.hospital
    ids = []
    if patient.national_id:
        ids.append({"type": "National ID", "value": patient.national_id})
    if patient.passport_number:
        ids.append({"type": "Passport", "value": patient.passport_number})
    if patient.birth_certificate_number:
        ids.append({"type": "Birth Certificate", "value": patient.birth_certificate_number})
    s = {
        "generated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat(),
        "facility": {"id": h.id, "name": h.name, "mfl_code": h.mfl_code, "level": h.level, "county": h.county,
                     "sub_county": h.sub_county, "phone": h.phone},
        "patient": {"id": patient.id, "patient_number": patient.patient_number, "first_name": patient.first_name,
                    "last_name": patient.last_name, "gender": patient.gender, "dob": _d(patient.date_of_birth),
                    "age": patient.age, "estimated_age_years": patient.estimated_age_years, "identifiers": ids,
                    "dha_client_id": patient.dha_client_id, "phone": patient.phone, "email": patient.email,
                    "address": patient.address, "county": patient.county, "sub_county": patient.sub_county,
                    "ward": patient.ward, "village": patient.village, "nationality": patient.nationality,
                    "blood_group": patient.blood_group,
                    "next_of_kin": {"name": patient.next_of_kin_name, "phone": patient.next_of_kin_phone,
                                    "relationship": patient.next_of_kin_relationship}},
        "allergy_status": patient.allergy_status or "Unknown",
    }
    s["problems"] = [{"id": p.id, "description": p.description, "code": p.code_display,
                      "icd11": p.diagnosis_code.icd11_code if p.diagnosis_code else None,
                      "snomed": p.diagnosis_code.snomed_ct_code if p.diagnosis_code else None,
                      "status": p.status, "onset": _d(p.onset_date), "resolved": _d(p.resolved_date)}
                     for p in PatientProblem.query.filter_by(patient_id=patient.id).order_by(PatientProblem.id).all()]
    s["problems_checked"] = bool(s["problems"])
    s["allergies"] = [{"id": a.id, "name": a.allergen_name, "type": a.allergen_type, "reaction": a.reaction,
                       "severity": a.severity, "status": a.status, "snomed": a.snomed_ct_code, "hpt": a.hpt_code}
                      for a in PatientAllergy.query.filter_by(patient_id=patient.id).order_by(PatientAllergy.id).all()
                      if a.status != "Entered in error"]
    s["medications"] = [{"id": m.id, "name": m.display_name, "hpt_code": m.hpt_code, "dosage": m.dosage,
                         "frequency": m.frequency, "route": m.route, "status": m.effective_status,
                         "start": _d(m.start_date), "end": _d(m.end_date), "indication": m.indication}
                        for m in PatientMedication.query.filter_by(patient_id=patient.id).order_by(PatientMedication.id).all()]
    rxs = Prescription.query.filter_by(patient_id=patient.id).order_by(Prescription.created_at.desc()).limit(10).all()
    s["prescriptions"] = [{"id": rx.id, "date": _d(rx.created_at.date()), "prescriber": rx.doctor.full_name if rx.doctor else None,
                           "items": [{"name": it.effective_drug.name, "hpt_code": it.effective_drug.hpt_code,
                                      "dosage": it.effective_dosage, "frequency": it.frequency, "duration": it.duration,
                                      "quantity": it.quantity_prescribed, "instructions": it.instructions}
                                     for it in rx.items if it.status != "Cancelled"]} for rx in rxs]
    s["vitals"] = [{"id": t.id, "date": t.created_at.replace(microsecond=0).isoformat(), "temperature_c": t.temperature_c,
                    "pulse_bpm": t.pulse_bpm, "bp_systolic": t.bp_systolic, "bp_diastolic": t.bp_diastolic,
                    "respiratory_rate": t.respiratory_rate, "spo2_percent": t.spo2_percent, "weight_kg": t.weight_kg,
                    "height_cm": t.height_cm, "bmi": t.bmi}
                   for t in Triage.query.filter_by(patient_id=patient.id).order_by(Triage.created_at.desc()).limit(5).all()]
    labs = LabOrder.query.filter_by(patient_id=patient.id, status="Result Ready").order_by(LabOrder.resulted_at.desc()).limit(30).all()
    s["labs"] = [{"id": o.id, "name": o.lab_test.name, "loinc": o.lab_test.loinc_code, "value": o.result_value,
                  "unit": o.lab_test.unit, "date": _d(o.resulted_at.date()) if o.resulted_at else None} for o in labs]
    s["care_plans"] = [{"problem": c.problem, "goal": c.goal, "interventions": c.interventions, "status": c.status}
                       for c in CarePlan.query.join(Admission, Admission.id == CarePlan.admission_id)
                       .filter(Admission.patient_id == patient.id).order_by(CarePlan.id.desc()).limit(20).all()]
    vs = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.created_at.desc()).limit(max_visits).all()
    s["encounters"] = [{"id": v.id, "type": v.visit_type, "date": v.created_at.replace(microsecond=0).isoformat(),
                        "end": v.closed_at.replace(microsecond=0).isoformat() if v.closed_at else None, "reason": v.reason,
                        "status": v.status} for v in vs]
    s["immunizations"] = [{"id": i.id, "vaccine": i.vaccine, "dose": i.dose_number, "date": _d(i.date_given),
                           "batch": i.batch_number}
                          for i in ImmunizationRecord.query.filter_by(patient_id=patient.id).order_by(ImmunizationRecord.date_given).all()]
    return s
