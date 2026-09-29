from app.extensions import db
from app.models import now


class BackupRun(db.Model):
    __tablename__ = "backup_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=now, nullable=False)
    finished_at = db.Column(db.DateTime)
    kind = db.Column(db.String(10), default="full")
    trigger = db.Column(db.String(12), default="manual")     # manual / scheduled
    status = db.Column(db.String(12), default="Running")     # Running / OK / Failed
    engine = db.Column(db.String(12))                        # sqlite / mysql / postgresql
    file_name = db.Column(db.String(255))
    size_bytes = db.Column(db.BigInteger)
    sha256 = db.Column(db.String(64))
    encrypted = db.Column(db.Boolean, default=True)
    key_id = db.Column(db.String(40))
    offsite_status = db.Column(db.String(60))                # e.g. "copied to /mnt/x", "s3 ok", "not configured"
    table_counts = db.Column(db.Text)                        # JSON, used by restore verification
    error = db.Column(db.String(500))
    verified_at = db.Column(db.DateTime)
    verify_status = db.Column(db.String(60))                 # "restore test passed" / "checksum only" / failure text
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))


class DisasterRecoveryPlan(db.Model):
    """Single platform-level record describing the DR plan and its last test."""
    __tablename__ = "disaster_recovery_plan"

    id = db.Column(db.Integer, primary_key=True)
    backup_frequency = db.Column(db.String(20), default="Daily")
    rto_hours = db.Column(db.Float)
    rpo_hours = db.Column(db.Float)
    plan_reference = db.Column(db.String(300))               # where the written DR plan document lives
    last_test_date = db.Column(db.Date)
    last_test_result = db.Column(db.String(300))
    tested_by = db.Column(db.String(150))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)
