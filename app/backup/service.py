"""DB-aware wrapper: records each run, copies offsite, verifies by restore test."""
import datetime
import json

from flask import current_app

from app.extensions import db
from app.models import log_action
from app.backup import engine
from app.backup.models import BackupRun, DisasterRecoveryPlan


def run_backup_job(user=None, trigger="manual"):
    cfg = current_app.config
    run = BackupRun(trigger=trigger, created_by_id=user.id if user else None)
    db.session.add(run)
    db.session.commit()
    try:
        res = engine.run_backup(cfg["SQLALCHEMY_DATABASE_URI"], cfg["BACKUP_DIR"], cfg["BACKUP_KEEP_DAYS"])
        run.engine, run.file_name, run.size_bytes, run.sha256 = res["engine"], res["file_name"], res["size"], res["sha256"]
        run.key_id, run.table_counts = res["key_id"], json.dumps(res["table_counts"]) if res["table_counts"] else None
        try:
            run.offsite_status = engine.copy_offsite(res["file"], cfg.get("BACKUP_OFFSITE_DIR") or None,
                                                     cfg.get("BACKUP_S3_BUCKET") or None, cfg.get("BACKUP_S3_PREFIX", ""),
                                                     cfg.get("BACKUP_S3_ENDPOINT_URL") or None)
        except engine.BackupError as e:
            run.offsite_status = f"FAILED: {e}"
        ok, msg = engine.verify_backup(res["file"], res["sha256"], res["table_counts"])
        run.verify_status, run.verified_at = msg, datetime.datetime.utcnow()
        run.status = "OK" if ok else "Failed"
        if not ok:
            run.error = msg[:500]
    except Exception as e:  # noqa: BLE001 — record any failure rather than lose the run
        run.status, run.error = "Failed", f"{e.__class__.__name__}: {e}"[:500]
    run.finished_at = datetime.datetime.utcnow()
    log_action(user, "backup_run", "BackupRun", run.id, {"status": run.status, "trigger": trigger})
    db.session.commit()
    return run


def get_plan():
    plan = DisasterRecoveryPlan.query.first()
    if not plan:
        plan = DisasterRecoveryPlan()
        db.session.add(plan)
        db.session.flush()
    return plan


def summary():
    cfg = current_app.config
    last_ok = BackupRun.query.filter_by(status="OK").order_by(BackupRun.id.desc()).first()
    return {"last_ok": last_ok, "age_hours": engine.latest_backup_age_hours(cfg["BACKUP_DIR"]),
            "offsite": bool(cfg.get("BACKUP_OFFSITE_DIR") or cfg.get("BACKUP_S3_BUCKET")),
            "offsite_ok": bool(last_ok and last_ok.offsite_status and "FAILED" not in last_ok.offsite_status
                               and last_ok.offsite_status != "not configured"),
            "plan": DisasterRecoveryPlan.query.first()}
