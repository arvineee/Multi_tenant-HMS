import datetime
import json

from flask import Blueprint, render_template, request, jsonify, abort, current_app, Response
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import Hospital, Patient, log_action
from app.reporting import service as svc, logic
from app.reporting.models import (NotifiableDisease, DiseaseNotification, IdsrWeeklyReport, PublicHealthEvent,
                                  PublicHealthEventUpdate, QualityMeasure, QualityMeasureResult, SubmissionLog,
                                  EVENT_TYPES, EVENT_STATUSES, ALERT_LEVELS)

reporting_bp = Blueprint("reporting", __name__, template_folder="../templates/reporting")


def _data():
    return request.get_json(silent=True) or request.form


def _hosp_ids():
    return current_user.accessible_hospital_ids()


def _hospital(hid=None):
    hid = hid or request.args.get("hospital_id", type=int) or current_user.hospital_id
    if hid not in _hosp_ids():
        abort(403)
    return db.session.get(Hospital, hid) or abort(404)


def _mode_note():
    m = (current_app.config.get("SURVEILLANCE_MODE") or "off").lower()
    return {"off": "Submission is OFF: reports are prepared and stored only.",
            "mock": "MOCK mode: reports are marked submitted but nothing is transmitted.",
            "live": "LIVE mode: reports are transmitted to the configured surveillance endpoint."}.get(m, m)


# ---------------------------------------------------------------- notifications
@reporting_bp.route("/surveillance")
@login_required
@permission_required("surveillance.manage")
def dashboard():
    ids = _hosp_ids()
    notes = (DiseaseNotification.query.filter(DiseaseNotification.hospital_id.in_(ids))
             .order_by(DiseaseNotification.detected_at.desc()).limit(200).all())
    open_n = [n for n in notes if n.status != "Submitted"]
    late = [n for n in open_n if n.hours_open >= 24]
    weekly = IdsrWeeklyReport.query.filter(IdsrWeeklyReport.hospital_id.in_(ids)).order_by(
        IdsrWeeklyReport.epi_year.desc(), IdsrWeeklyReport.epi_week.desc()).limit(20).all()
    events = PublicHealthEvent.query.filter(PublicHealthEvent.hospital_id.in_(ids)).order_by(
        PublicHealthEvent.created_at.desc()).limit(50).all()
    y, w, _, _ = logic.previous_epi_week()
    return render_template("reporting/dashboard.html", notes=notes, open_n=open_n, late=late, weekly=weekly, events=events,
                           mode_note=_mode_note(), prev_week=(y, w), event_types=EVENT_TYPES, event_statuses=EVENT_STATUSES,
                           alert_levels=ALERT_LEVELS, hospitals=Hospital.query.filter(Hospital.id.in_(ids)).all())


@reporting_bp.route("/surveillance/notifications/<int:nid>", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def update_notification(nid):
    n = db.session.get(DiseaseNotification, nid) or abort(404)
    if n.hospital_id not in _hosp_ids():
        abort(403)
    if n.status == "Submitted":
        return jsonify(success=False, error="Already submitted; it can't be edited."), 400
    d = _data()
    try:
        n.date_of_onset = datetime.date.fromisoformat(d["date_of_onset"]) if d.get("date_of_onset") else n.date_of_onset
    except ValueError:
        return jsonify(success=False, error="Date of onset must be YYYY-MM-DD."), 400
    if d.get("lab_confirmed") in ("true", "false", True, False):
        n.lab_confirmed = str(d["lab_confirmed"]).lower() == "true"
    if d.get("outcome") in ("Alive", "Died", "Unknown"):
        n.outcome = d["outcome"]
    if d.get("vaccination_status") in ("Vaccinated", "Not vaccinated", "Unknown"):
        n.vaccination_status = d["vaccination_status"]
    n.notes = (d.get("notes") or "")[:500] or n.notes
    log_action(current_user, "update", "DiseaseNotification", n.id, patient_id=n.patient_id)
    db.session.commit()
    return jsonify(success=True)


@reporting_bp.route("/surveillance/notifications/<int:nid>/submit", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def submit_notification(nid):
    n = db.session.get(DiseaseNotification, nid) or abort(404)
    if n.hospital_id not in _hosp_ids():
        abort(403)
    status = svc.submit_notification(n, current_app.config)
    log_action(current_user, "submit", "DiseaseNotification", n.id, {"status": status}, patient_id=n.patient_id)
    db.session.commit()
    return jsonify(success=status == "Submitted", status=status, error=n.last_error)


@reporting_bp.route("/surveillance/notifications/retry", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def retry_all():
    n = svc.retry_pending_notifications(current_app.config)
    db.session.commit()
    return jsonify(success=True, attempted=n)


@reporting_bp.route("/surveillance/diseases", methods=["GET", "POST"])
@login_required
@permission_required("surveillance.manage")
def diseases():
    if request.method == "POST":
        if not current_user.has_permission("compliance.view") and not current_user.has_permission("security.manage"):
            abort(403)
        d = _data()
        row = db.session.get(NotifiableDisease, int(d["id"])) if d.get("id") else None
        if d.get("action") == "toggle" and row:
            row.is_active = not row.is_active
        elif d.get("action") == "save":
            code = (d.get("code") or "").strip().upper()
            if not code or not (d.get("name") or "").strip() or not (d.get("icd10_prefixes") or "").strip():
                return jsonify(success=False, error="Code, name and ICD-10 prefixes are required."), 400
            row = row or NotifiableDisease.query.filter_by(code=code).first() or NotifiableDisease(code=code)
            row.name, row.icd10_prefixes = d["name"].strip()[:150], d["icd10_prefixes"].strip()[:200]
            row.category = "weekly" if d.get("category") == "weekly" else "immediate"
            row.moh_form = (d.get("moh_form") or "")[:20] or None
            db.session.add(row)
        else:
            return jsonify(success=False, error="Nothing to do."), 400
        log_action(current_user, "update", "NotifiableDisease", None, {"action": d.get("action")})
        db.session.commit()
        return jsonify(success=True)
    return render_template("reporting/diseases.html", diseases=NotifiableDisease.query.order_by(
        NotifiableDisease.category, NotifiableDisease.sort_order).all())


# ---------------------------------------------------------------- IDSR weekly
@reporting_bp.route("/surveillance/weekly/generate", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def weekly_generate():
    d = _data()
    h = _hospital(int(d.get("hospital_id") or current_user.hospital_id))
    try:
        y, w = int(d["year"]), int(d["week"])
        datetime.date.fromisocalendar(y, w, 1)
    except (KeyError, ValueError):
        return jsonify(success=False, error="Enter a valid year and epidemiological week."), 400
    rep, changed = svc.generate_weekly(h, y, w, current_user.id)
    log_action(current_user, "generate", "IdsrWeeklyReport", rep.id, {"week": rep.label})
    db.session.commit()
    return jsonify(success=True, id=rep.id, changed=changed)


@reporting_bp.route("/surveillance/weekly/<int:rid>")
@login_required
@permission_required("surveillance.manage")
def weekly_view(rid):
    rep = db.session.get(IdsrWeeklyReport, rid) or abort(404)
    if rep.hospital_id not in _hosp_ids():
        abort(403)
    return render_template("reporting/weekly.html", rep=rep, payload=json.loads(rep.payload or "{}"),
                           diseases={d.code: d for d in NotifiableDisease.query.all()}, mode_note=_mode_note())


@reporting_bp.route("/surveillance/weekly/<int:rid>/submit", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def weekly_submit(rid):
    rep = db.session.get(IdsrWeeklyReport, rid) or abort(404)
    if rep.hospital_id not in _hosp_ids():
        abort(403)
    if rep.status == "Submitted":
        return jsonify(success=False, error="This week was already submitted."), 400
    status = svc.submit_weekly(rep, current_app.config, current_user.id)
    log_action(current_user, "submit", "IdsrWeeklyReport", rep.id, {"status": status})
    db.session.commit()
    return jsonify(success=status == "Submitted", status=status, error=rep.last_error)


@reporting_bp.route("/surveillance/weekly/<int:rid>/export.<fmt>")
@login_required
@permission_required("surveillance.manage")
def weekly_export(rid, fmt):
    rep = db.session.get(IdsrWeeklyReport, rid) or abort(404)
    if rep.hospital_id not in _hosp_ids():
        abort(403)
    log_action(current_user, "export", "IdsrWeeklyReport", rep.id, {"format": fmt})
    db.session.commit()
    if fmt == "json":
        return Response(json.dumps(svc.weekly_body(rep), indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=idsr-{rep.label}.json"})
    if fmt == "xml":
        return Response(svc.weekly_sdmx(rep), mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=idsr-{rep.label}-sdmx.xml"})
    abort(404)


# ---------------------------------------------------------------- public health events
@reporting_bp.route("/surveillance/events", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def add_event():
    d = _data()
    h = _hospital(int(d.get("hospital_id") or current_user.hospital_id))
    if d.get("event_type") not in EVENT_TYPES or not (d.get("title") or "").strip():
        return jsonify(success=False, error="Choose the event type and give it a title."), 400
    try:
        cases, deaths = int(d.get("cases") or 0), int(d.get("deaths") or 0)
        when = datetime.date.fromisoformat(d["date_detected"]) if d.get("date_detected") else datetime.date.today()
    except ValueError:
        return jsonify(success=False, error="Check the numbers and date."), 400
    if cases < 0 or deaths < 0 or deaths > cases and cases:
        return jsonify(success=False, error="Deaths can't exceed cases, and numbers can't be negative."), 400
    e = PublicHealthEvent(hospital_id=h.id, event_type=d["event_type"], title=d["title"].strip()[:200],
                          description=d.get("description"), date_detected=when, location=(d.get("location") or "")[:200] or None,
                          cases=cases, deaths=deaths, alert_level=d.get("alert_level") if d.get("alert_level") in ALERT_LEVELS else "Medium",
                          reported_to=(d.get("reported_to") or "")[:100] or None, created_by_id=current_user.id,
                          reported_at=datetime.datetime.utcnow() if d.get("reported_to") else None)
    db.session.add(e)
    db.session.flush()
    log_action(current_user, "create", "PublicHealthEvent", e.id, {"title": e.title})
    db.session.commit()
    return jsonify(success=True, id=e.id)


@reporting_bp.route("/surveillance/events/<int:eid>/update", methods=["POST"])
@login_required
@permission_required("surveillance.manage")
def update_event(eid):
    e = db.session.get(PublicHealthEvent, eid) or abort(404)
    if e.hospital_id not in _hosp_ids():
        abort(403)
    d = _data()
    note = (d.get("note") or "").strip()
    if not note:
        return jsonify(success=False, error="Write an update note."), 400
    status = d.get("status") if d.get("status") in EVENT_STATUSES else None
    try:
        cases = int(d["cases"]) if d.get("cases") not in (None, "") else None
        deaths = int(d["deaths"]) if d.get("deaths") not in (None, "") else None
    except ValueError:
        return jsonify(success=False, error="Cases and deaths must be numbers."), 400
    db.session.add(PublicHealthEventUpdate(event_id=e.id, note=note[:1000], status=status, cases=cases, deaths=deaths,
                                           created_by_id=current_user.id))
    if status:
        e.status = status
    if cases is not None:
        e.cases = cases
    if deaths is not None:
        e.deaths = deaths
    log_action(current_user, "update", "PublicHealthEvent", e.id, {"status": status})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------- quality measures
@reporting_bp.route("/quality")
@login_required
@permission_required("quality.manage")
def quality():
    h = _hospital()
    today = datetime.date.today()
    start = datetime.date.fromisoformat(request.args["start"]) if request.args.get("start") else today.replace(day=1) - datetime.timedelta(days=1)
    start = start.replace(day=1) if not request.args.get("start") else start
    end = datetime.date.fromisoformat(request.args["end"]) if request.args.get("end") else (
        today.replace(day=1) - datetime.timedelta(days=1))
    results = (QualityMeasureResult.query.filter_by(hospital_id=h.id, period_start=start, period_end=end).all())
    have = {r.measure_id: r for r in results}
    measures = QualityMeasure.query.filter_by(is_active=True).order_by(QualityMeasure.code).all()
    return render_template("reporting/quality.html", hospital=h, start=start, end=end, measures=measures, have=have,
                           mode_note=_mode_note(), hospitals=Hospital.query.filter(Hospital.id.in_(_hosp_ids())).all())


def _period():
    d = _data()
    try:
        s, e = datetime.date.fromisoformat(d["start"]), datetime.date.fromisoformat(d["end"])
    except (KeyError, ValueError):
        raise ValueError("Enter the period start and end as YYYY-MM-DD.")
    if e < s:
        raise ValueError("The period end is before its start.")
    return s, e


@reporting_bp.route("/quality/calculate", methods=["POST"])
@login_required
@permission_required("quality.manage")
def quality_calculate():
    try:
        s, e = _period()
    except ValueError as ex:
        return jsonify(success=False, error=str(ex)), 400
    h = _hospital(int(_data().get("hospital_id") or current_user.hospital_id))
    svc.calculate_measures(h.id, s, e, current_user.id)
    log_action(current_user, "calculate", "QualityMeasureResult", None, {"period": f"{s}..{e}"})
    db.session.commit()
    return jsonify(success=True)


@reporting_bp.route("/quality/capture", methods=["POST"])
@login_required
@permission_required("quality.manage")
def quality_capture():
    d = _data()
    try:
        s, e = _period()
        m = db.session.get(QualityMeasure, int(d["measure_id"])) or abort(404)
        h = _hospital(int(d.get("hospital_id") or current_user.hospital_id))
        svc.capture_result(m, h.id, s, e, int(d["numerator"]), int(d["denominator"]), d.get("notes"), current_user.id)
    except (ValueError, KeyError) as ex:
        return jsonify(success=False, error=str(ex) or "Check the numbers."), 400
    log_action(current_user, "capture", "QualityMeasureResult", None, {"measure": m.code})
    db.session.commit()
    return jsonify(success=True)


@reporting_bp.route("/quality/import", methods=["POST"])
@login_required
@permission_required("quality.manage")
def quality_import():
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="Choose a CSV file."), 400
    raw = f.read(2_000_000)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(success=False, error="The file must be UTF-8 text (CSV)."), 400
    h = _hospital(int(request.form.get("hospital_id") or current_user.hospital_id))
    n, errors = svc.import_results_csv(text, h.id, current_user.id)
    log_action(current_user, "import", "QualityMeasureResult", None, {"rows": n, "errors": len(errors)})
    db.session.commit()
    return jsonify(success=not errors or n > 0, imported=n, errors=errors[:20])


@reporting_bp.route("/quality/export.csv")
@login_required
@permission_required("quality.manage")
def quality_export():
    h = _hospital()
    rows = QualityMeasureResult.query.filter_by(hospital_id=h.id).order_by(
        QualityMeasureResult.period_start.desc(), QualityMeasureResult.measure_id).limit(1000).all()
    log_action(current_user, "export", "QualityMeasureResult", None, {"rows": len(rows)})
    db.session.commit()
    return Response(svc.export_results_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=quality-measures.csv"})


@reporting_bp.route("/quality/submit", methods=["POST"])
@login_required
@permission_required("quality.manage")
def quality_submit():
    try:
        s, e = _period()
    except ValueError as ex:
        return jsonify(success=False, error=str(ex)), 400
    h = _hospital(int(_data().get("hospital_id") or current_user.hospital_id))
    rows = QualityMeasureResult.query.filter(QualityMeasureResult.hospital_id == h.id, QualityMeasureResult.period_start == s,
                                             QualityMeasureResult.period_end == e,
                                             QualityMeasureResult.status != "Submitted").all()
    if not rows:
        return jsonify(success=False, error="Calculate or capture results for this period first."), 400
    status, err = svc.submit_results(rows, current_app.config)
    log_action(current_user, "submit", "QualityMeasureResult", None, {"status": status})
    db.session.commit()
    return jsonify(success=status in ("sent", "mock"), status=status, error=err)
