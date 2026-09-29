"""Surveillance and quality-measure services (DB layer)."""
import csv
import datetime
import io
import json

from sqlalchemy import func

from app.extensions import db
from app.models import (Hospital, Patient, Visit, Consultation, Triage, Admission, log_action)
from app.reporting import logic
from app.reporting.models import (NotifiableDisease, DiseaseNotification, IdsrWeeklyReport, PublicHealthEvent,
                                  QualityMeasure, QualityMeasureResult, SubmissionLog)

AUTO_SIGNAL_THRESHOLD = 3   # same immediate disease, same facility, within 7 days -> raise an event


def seed_notifiable_diseases():
    n = 0
    for i, (code, name, cat, prefixes, form) in enumerate(logic.DEFAULT_NOTIFIABLE):
        if not NotifiableDisease.query.filter_by(code=code).first():
            db.session.add(NotifiableDisease(code=code, name=name, category=cat, icd10_prefixes=prefixes,
                                             moh_form=form, sort_order=i))
            n += 1
    return n


BUILTIN_MEASURES = [
    ("QM-DX", "Completed consultations with a diagnosis", "Share of finalised consultations that carry a coded diagnosis.",
     "Consultations with a diagnosis", "Finalised consultations", True, 95.0, "dx_recorded"),
    ("QM-ALLERGY", "Patients with allergy status documented", "Share of patients seen whose allergy status is not 'Unknown'.",
     "Patients with status recorded", "Patients seen", True, 90.0, "allergy_documented"),
    ("QM-VITALS", "Triaged visits with weight and blood pressure", "Vital-sign completeness at triage.",
     "Triage records with weight and BP", "Triage records", True, 90.0, "vitals_complete"),
    ("QM-NOTIFY24", "Immediate notifications submitted within 24 hours", "Timeliness of immediate reportable disease notification.",
     "Submitted within 24h", "Immediate notifications", True, 100.0, "notify_timely"),
    ("QM-DSUM", "Discharges with a discharge summary", "Inpatient discharges that have a discharge summary issued.",
     "Discharges with summary", "Discharges", True, 95.0, "discharge_summary"),
]


def seed_quality_measures():
    n = 0
    for code, name, desc, num, den, hib, target, key in BUILTIN_MEASURES:
        if not QualityMeasure.query.filter_by(code=code).first():
            db.session.add(QualityMeasure(code=code, name=name, description=desc, numerator_desc=num,
                                          denominator_desc=den, higher_is_better=hib, target_percent=target, builtin_key=key))
            n += 1
    return n


def _dt(d, end=False):
    return datetime.datetime.combine(d, datetime.time.max if end else datetime.time.min)


def detect_notifiable(consultation, user_id=None):
    """Called when a consultation is finalised. Creates a notification for each
    matching diagnosis (primary and secondary). Returns the new notifications."""
    diseases = NotifiableDisease.query.filter_by(is_active=True).all()
    visit = consultation.visit
    created = []
    for dx in consultation.all_diagnoses:
        d = logic.match_disease(dx.code, diseases)
        if not d:
            continue
        if DiseaseNotification.query.filter_by(consultation_id=consultation.id, disease_id=d.id).first():
            continue
        if d.category != "immediate":
            continue  # weekly-category diseases are counted in the IDSR weekly aggregate, not notified per case
        n = DiseaseNotification(
            hospital_id=consultation.hospital_id, patient_id=consultation.patient_id, visit_id=visit.id if visit else None,
            consultation_id=consultation.id, disease_id=d.id, diagnosis_code=dx.code,
            date_seen=datetime.date.today(), created_by_id=user_id, status="Pending",
            outcome=visit.outcome if visit else None,
        )
        db.session.add(n)
        created.append(n)
    db.session.flush()
    for n in created:
        log_action(None, "notification_created", "DiseaseNotification", n.id, {"disease": n.disease.code},
                   patient_id=n.patient_id)
        check_auto_signal(n)
    return created


def check_auto_signal(n):
    since = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    cnt = DiseaseNotification.query.filter(DiseaseNotification.hospital_id == n.hospital_id,
                                           DiseaseNotification.disease_id == n.disease_id,
                                           DiseaseNotification.detected_at >= since).count()
    if cnt >= AUTO_SIGNAL_THRESHOLD:
        title = f"Cluster signal: {cnt} {n.disease.name} cases in 7 days"
        if not PublicHealthEvent.query.filter(PublicHealthEvent.hospital_id == n.hospital_id,
                                              PublicHealthEvent.title == title).first():
            db.session.add(PublicHealthEvent(hospital_id=n.hospital_id, event_type="Disease outbreak / cluster", title=title,
                                             description="Raised automatically from immediate notifications.",
                                             cases=cnt, alert_level="High", source="Auto-signal"))


def _http(cfg):
    import requests
    return requests


def _submit(cfg, kind, ref_id, hospital_id, path_key, body, transport=None):
    """Returns (status, error). status: sent / mock / not_configured / failed. Logs every attempt."""
    mode = (cfg.get("SURVEILLANCE_MODE") or "off").lower()
    log = SubmissionLog(kind=kind, ref_id=ref_id, hospital_id=hospital_id, mode=mode)
    if mode == "off" or (mode == "live" and not cfg.get("SURVEILLANCE_URL")):
        log.status = "skipped"
        db.session.add(log)
        return "not_configured", "Surveillance submission is not configured (SURVEILLANCE_MODE / SURVEILLANCE_URL)."
    if mode == "mock":
        log.status, log.response_excerpt = "sent", "mock: not transmitted"
        db.session.add(log)
        return "mock", None
    headers = {"Content-Type": "application/json"}
    if cfg.get("SURVEILLANCE_TOKEN"):
        headers["Authorization"] = "Bearer " + cfg["SURVEILLANCE_TOKEN"]
    auth = (cfg["SURVEILLANCE_USERNAME"], cfg.get("SURVEILLANCE_PASSWORD", "")) if cfg.get("SURVEILLANCE_USERNAME") else None
    try:
        r = (transport or _http(cfg)).post(cfg["SURVEILLANCE_URL"] + cfg.get(path_key, ""), data=json.dumps(body),
                                            headers=headers, auth=auth, timeout=15)
        log.http_status, log.response_excerpt = r.status_code, (r.text or "")[:500]
        ok = 200 <= r.status_code < 300
        log.status = "sent" if ok else "failed"
        db.session.add(log)
        return ("sent", None) if ok else ("failed", f"Server returned {r.status_code}.")
    except Exception as e:
        log.status, log.response_excerpt = "failed", f"{e.__class__.__name__}: {e}"[:500]
        db.session.add(log)
        return "failed", "Could not reach the surveillance server."


def notification_payload(n):
    p, h = n.patient, n.hospital
    return {"type": "immediate_notification", "form": n.disease.moh_form, "disease_code": n.disease.code,
            "disease": n.disease.name, "icd10": n.diagnosis_code,
            "facility": {"name": h.name, "mfl_code": h.mfl_code, "county": h.county, "sub_county": h.sub_county},
            "case": {"case_id": f"{h.code}-{n.id}", "age_years": p.age, "sex": p.gender, "county": p.county,
                     "sub_county": p.sub_county, "ward": p.ward, "village": p.village,
                     "date_of_onset": n.date_of_onset.isoformat() if n.date_of_onset else None,
                     "date_seen": n.date_seen.isoformat() if n.date_seen else None,
                     "lab_confirmed": n.lab_confirmed, "outcome": n.outcome, "vaccination_status": n.vaccination_status},
            "detected_at": n.detected_at.isoformat()}


def submit_notification(n, cfg, transport=None):
    status, err = _submit(cfg, "notification", n.id, n.hospital_id, "SURVEILLANCE_PATH_NOTIFY", notification_payload(n), transport)
    n.attempts = (n.attempts or 0) + 1
    if status in ("sent", "mock"):
        n.status, n.submitted_at, n.last_error = "Submitted", datetime.datetime.utcnow(), None
        n.submission_reference = "mock" if status == "mock" else n.submission_reference
    elif status == "not_configured":
        n.status, n.last_error = "Not configured", err
    else:
        n.status, n.last_error = "Failed", err
    return n.status


def generate_weekly(hospital, year, week, user_id=None):
    start = datetime.date.fromisocalendar(year, week, 1)
    end = start + datetime.timedelta(days=6)
    diseases = {d.id: d for d in NotifiableDisease.query.filter_by(is_active=True).all()}
    finalised = Consultation.query.filter(Consultation.hospital_id == hospital.id,
                                          Consultation.created_at >= _dt(start), Consultation.created_at <= _dt(end, True)).all()
    cases = []
    for c in finalised:
        if c.visit and c.visit.status not in ("Completed", "Admitted", "Discharged"):
            continue
        seen = set()
        for dx in c.all_diagnoses:
            d = logic.match_disease(dx.code, diseases.values())
            if d and d.id not in seen:
                seen.add(d.id)
                cases.append({"disease_code": d.code, "age_years": c.patient.age if c.patient else None,
                              "outcome": c.visit.outcome if c.visit else None})
    payload = logic.aggregate_weekly(cases)
    opd = Visit.query.filter(Visit.hospital_id == hospital.id, Visit.visit_type == "Outpatient",
                             Visit.created_at >= _dt(start), Visit.created_at <= _dt(end, True)).count()
    rep = IdsrWeeklyReport.query.filter_by(hospital_id=hospital.id, epi_year=year, epi_week=week).first()
    if rep and rep.status == "Submitted":
        return rep, False
    if not rep:
        rep = IdsrWeeklyReport(hospital_id=hospital.id, epi_year=year, epi_week=week, week_start=start, week_end=end)
        db.session.add(rep)
    rep.payload, rep.total_outpatient_visits = json.dumps(payload), opd
    rep.generated_at, rep.generated_by_id, rep.status = datetime.datetime.utcnow(), user_id, "Generated"
    db.session.flush()
    return rep, True


def weekly_body(rep):
    payload = json.loads(rep.payload or "{}")
    body = logic.dhis2_data_value_set(payload, rep.hospital.mfl_code or f"HOSP-{rep.hospital_id}", f"{rep.epi_year}W{rep.epi_week}")
    body["totalOutpatientVisits"] = rep.total_outpatient_visits
    return body


def weekly_sdmx(rep):
    payload = json.loads(rep.payload or "{}")
    return logic.build_sdmx_generic("IDSR_WEEKLY", "KE.MOH", "DSD_IDSR_WEEKLY",
                                    logic.weekly_to_series(payload, rep.hospital.mfl_code or f"HOSP-{rep.hospital_id}", rep.label))


def submit_weekly(rep, cfg, user_id=None, transport=None):
    status, err = _submit(cfg, "idsr_weekly", rep.id, rep.hospital_id, "SURVEILLANCE_PATH_WEEKLY", weekly_body(rep), transport)
    if status in ("sent", "mock"):
        rep.status, rep.submitted_at, rep.submitted_by_id, rep.last_error = "Submitted", datetime.datetime.utcnow(), user_id, None
    elif status == "not_configured":
        rep.status, rep.last_error = "Not configured", err
    else:
        rep.status, rep.last_error = "Failed", err
    return rep.status


def run_weekly_for_all(cfg, today=None):
    """Scheduled entry point (flask reporting-weekly): previous epi week, every active hospital."""
    y, w, _, _ = logic.previous_epi_week(today)
    out = []
    for h in Hospital.query.filter_by(is_active=True).all():
        rep, changed = generate_weekly(h, y, w)
        if changed and rep.status != "Submitted":
            submit_weekly(rep, cfg)
        out.append((h.name, rep.label, rep.status))
    return out


def retry_pending_notifications(cfg):
    n = 0
    for x in DiseaseNotification.query.filter(DiseaseNotification.status.in_(["Pending", "Failed", "Not configured"])).all():
        if x.disease.category == "immediate":
            submit_notification(x, cfg)
            n += 1
    return n


# --- quality measures ---------------------------------------------------------
def _calc(key, hospital_id, start, end):
    s, e = _dt(start), _dt(end, True)
    if key == "dx_recorded":
        q = Consultation.query.join(Visit, Visit.id == Consultation.visit_id).filter(
            Consultation.hospital_id == hospital_id, Consultation.created_at.between(s, e),
            Visit.status.in_(["Completed", "Admitted", "Discharged"]))
        return q.filter(Consultation.diagnosis_code_id.isnot(None)).count(), q.count()
    if key == "allergy_documented":
        ids = [r[0] for r in db.session.query(Visit.patient_id).filter(Visit.hospital_id == hospital_id,
                                                                     Visit.created_at.between(s, e)).distinct().all()]
        if not ids:
            return 0, 0
        num = Patient.query.filter(Patient.id.in_(ids), Patient.allergy_status.isnot(None), Patient.allergy_status != "Unknown").count()
        return num, len(ids)
    if key == "vitals_complete":
        q = Triage.query.filter(Triage.hospital_id == hospital_id, Triage.created_at.between(s, e))
        num = q.filter(Triage.weight_kg.isnot(None), Triage.bp_systolic.isnot(None), Triage.bp_diastolic.isnot(None)).count()
        return num, q.count()
    if key == "notify_timely":
        rows = (DiseaseNotification.query.join(NotifiableDisease).filter(
            DiseaseNotification.hospital_id == hospital_id, DiseaseNotification.detected_at.between(s, e),
            NotifiableDisease.category == "immediate").all())
        ok = sum(1 for r in rows if r.submitted_at and (r.submitted_at - r.detected_at).total_seconds() <= 24 * 3600)
        return ok, len(rows)
    if key == "discharge_summary":
        q = Admission.query.filter(Admission.hospital_id == hospital_id, Admission.status == "Discharged",
                                   Admission.actual_discharge_date.between(s, e))
        return q.filter(Admission.discharge_summary_document_id.isnot(None)).count(), q.count()
    raise ValueError(f"Unknown built-in measure {key}")


def calculate_measures(hospital_id, start, end, user_id=None):
    out = []
    for m in QualityMeasure.query.filter(QualityMeasure.builtin_key.isnot(None), QualityMeasure.is_active.is_(True)).all():
        num, den = _calc(m.builtin_key, hospital_id, start, end)
        r = QualityMeasureResult.query.filter_by(measure_id=m.id, hospital_id=hospital_id, period_start=start, period_end=end).first()
        if r and r.status == "Submitted":
            out.append(r)
            continue
        if not r:
            r = QualityMeasureResult(measure_id=m.id, hospital_id=hospital_id, period_start=start, period_end=end)
            db.session.add(r)
        r.numerator, r.denominator, r.source = num, den, "calculated"
        r.calculated_at, r.calculated_by_id, r.status = datetime.datetime.utcnow(), user_id, "Draft"
        out.append(r)
    return out


def capture_result(measure, hospital_id, start, end, numerator, denominator, notes, user_id, source="captured"):
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("Numerator must be between 0 and the denominator.")
    r = QualityMeasureResult.query.filter_by(measure_id=measure.id, hospital_id=hospital_id, period_start=start, period_end=end).first()
    if r and r.status == "Submitted":
        raise ValueError("That period was already submitted and can't be changed.")
    if not r:
        r = QualityMeasureResult(measure_id=measure.id, hospital_id=hospital_id, period_start=start, period_end=end)
        db.session.add(r)
    r.numerator, r.denominator, r.source, r.notes = numerator, denominator, source, (notes or None)
    r.calculated_by_id, r.calculated_at, r.status = user_id, datetime.datetime.utcnow(), "Draft"
    return r


def import_results_csv(text, hospital_id, user_id):
    """Columns: measure_code,period_start,period_end,numerator,denominator[,notes]. Returns (imported, errors)."""
    rd = csv.DictReader(io.StringIO(text))
    need = {"measure_code", "period_start", "period_end", "numerator", "denominator"}
    if not rd.fieldnames or not need <= set(rd.fieldnames):
        return 0, [f"CSV needs columns: {', '.join(sorted(need))}."]
    n, errors = 0, []
    for i, row in enumerate(rd, start=2):
        try:
            m = QualityMeasure.query.filter_by(code=(row["measure_code"] or "").strip()).first()
            if not m:
                raise ValueError(f"unknown measure {row['measure_code']!r}")
            capture_result(m, hospital_id, datetime.date.fromisoformat(row["period_start"].strip()),
                           datetime.date.fromisoformat(row["period_end"].strip()), int(row["numerator"]),
                           int(row["denominator"]), row.get("notes"), user_id, source="imported")
            n += 1
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"Row {i}: {e}")
    return n, errors


def export_results_csv(results):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["measure_code", "measure", "facility_mfl", "period_start", "period_end", "numerator", "denominator", "rate_percent", "source", "status"])
    for r in results:
        w.writerow([r.measure.code, r.measure.name, r.hospital.mfl_code or "", r.period_start, r.period_end,
                    r.numerator, r.denominator, r.rate if r.rate is not None else "", r.source, r.status])
    return buf.getvalue()


def submit_results(results, cfg, transport=None):
    if not results:
        raise ValueError("Nothing to submit.")
    hosp = results[0].hospital
    vals = [{"dataElement": r.measure.code, "value": f"{r.rate}" if r.rate is not None else "", "numerator": r.numerator,
             "denominator": r.denominator, "period": f"{r.period_start:%Y%m%d}-{r.period_end:%Y%m%d}"} for r in results]
    body = {"orgUnit": hosp.mfl_code or f"HOSP-{hosp.id}", "dataValues": vals}
    status, err = _submit(cfg, "quality_measure", None, hosp.id, "SURVEILLANCE_PATH_QUALITY", body, transport)
    now = datetime.datetime.utcnow()
    for r in results:
        if status in ("sent", "mock"):
            r.status, r.submitted_at = "Submitted", now
        else:
            r.status = "Not configured" if status == "not_configured" else "Failed"
    return status, err
