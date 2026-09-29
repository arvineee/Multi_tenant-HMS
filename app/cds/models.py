from app.extensions import db
from app.models import now


class CdsRuleSetting(db.Model):
    """Lets an organization's clinical lead switch individual rules off."""
    __tablename__ = "cds_rule_settings"
    __table_args__ = (db.UniqueConstraint("organization_id", "rule_id", name="uq_cds_rule_setting"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    rule_id = db.Column(db.String(40), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)


class CdsAlertLog(db.Model):
    """What alerts were raised and what the clinician did about them."""
    __tablename__ = "cds_alert_log"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), index=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    rule_id = db.Column(db.String(40), nullable=False)
    severity = db.Column(db.String(10))
    message = db.Column(db.String(500))
    action = db.Column(db.String(12), default="shown")        # shown / overridden
    override_reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now)
