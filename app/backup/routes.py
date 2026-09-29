import datetime

from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import log_action
from app.backup import service as svc
from app.backup.models import BackupRun

backup_bp = Blueprint("backup", __name__, template_folder="../templates/backup")


@backup_bp.route("/system/backups")
@login_required
@permission_required("backup.manage")
def index():
    cfg = current_app.config
    return render_template("backup/index.html", runs=BackupRun.query.order_by(BackupRun.id.desc()).limit(50).all(),
                           plan=svc.get_plan(), info=svc.summary(),
                           offsite=bool(cfg.get("BACKUP_OFFSITE_DIR") or cfg.get("BACKUP_S3_BUCKET")),
                           keep_days=cfg["BACKUP_KEEP_DAYS"], derived_key=not cfg.get("DATA_ENCRYPTION_KEYS"))


@backup_bp.route("/system/backups/run", methods=["POST"])
@login_required
@permission_required("backup.manage")
def run():
    r = svc.run_backup_job(current_user, "manual")
    return jsonify(success=r.status == "OK", status=r.status, detail=r.verify_status or r.error, offsite=r.offsite_status)


@backup_bp.route("/system/backups/plan", methods=["POST"])
@login_required
@permission_required("backup.manage")
def save_plan():
    d = request.get_json(silent=True) or request.form
    plan = svc.get_plan()
    try:
        plan.rto_hours = float(d["rto_hours"]) if d.get("rto_hours") not in (None, "") else None
        plan.rpo_hours = float(d["rpo_hours"]) if d.get("rpo_hours") not in (None, "") else None
        plan.last_test_date = datetime.date.fromisoformat(d["last_test_date"]) if d.get("last_test_date") else None
    except (ValueError, KeyError):
        return jsonify(success=False, error="Check the hours and the date (YYYY-MM-DD)."), 400
    if (plan.rto_hours or 0) < 0 or (plan.rpo_hours or 0) < 0:
        return jsonify(success=False, error="Hours can't be negative."), 400
    if d.get("backup_frequency") in ("Hourly", "Daily", "Weekly"):
        plan.backup_frequency = d["backup_frequency"]
    plan.plan_reference = (d.get("plan_reference") or "")[:300] or None
    plan.last_test_result, plan.tested_by = (d.get("last_test_result") or "")[:300] or None, (d.get("tested_by") or "")[:150] or None
    plan.updated_by_id = current_user.id
    log_action(current_user, "update", "DisasterRecoveryPlan", plan.id)
    db.session.commit()
    return jsonify(success=True)
