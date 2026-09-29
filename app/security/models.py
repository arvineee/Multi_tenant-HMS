"""Security & privacy data: MFA enrolment, linked Digital Health ID, emergency
access grants, record version history, per-organization security policy and the
data-protection (ODPC / DPIA) register."""
import datetime

from app.extensions import db
from app.models import now
from app.security.crypto import EncryptedText


class UserMfa(db.Model):
    """One row per user who has started or completed TOTP enrolment."""
    __tablename__ = "user_mfa"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    secret = db.Column(EncryptedText, nullable=False)          # base32 TOTP secret, encrypted at rest
    is_enabled = db.Column(db.Boolean, default=False)          # true only after the first code is verified
    backup_code_hashes = db.Column(db.Text, default="[]")      # JSON list of HMAC hashes (single use)
    last_used_step = db.Column(db.BigInteger)                  # blocks replay of a code
    enrolled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)

    user = db.relationship("User", backref=db.backref("mfa", uselist=False))


class ExternalIdentity(db.Model):
    """Links a MediCore account to an identity at DHA's Digital Health ID service."""
    __tablename__ = "external_identities"
    __table_args__ = (db.UniqueConstraint("provider", "subject", name="uq_external_identity"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, default="dha")
    subject = db.Column(db.String(200), nullable=False)
    linked_at = db.Column(db.DateTime, default=now)

    user = db.relationship("User", backref=db.backref("external_identities", lazy=True))


REVIEW_OUTCOMES = ["Pending", "Justified", "Not justified"]


class EmergencyAccessGrant(db.Model):
    """'Break-glass': time-limited read access to a record the user would not
    normally see. Always needs a reason, is always audited, and lands in a
    review queue for a manager."""
    __tablename__ = "emergency_access_grants"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)  # the patient's hospital
    reason = db.Column(db.String(500), nullable=False)
    granted_at = db.Column(db.DateTime, default=now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime)
    review_outcome = db.Column(db.String(20), default="Pending")
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.String(500))

    user = db.relationship("User", foreign_keys=[user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    patient = db.relationship("Patient")

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > now()


class RecordVersion(db.Model):
    """Field-level history of clinical records (see app/security/versioning.py).
    Append-only."""
    __tablename__ = "record_versions"
    __table_args__ = (db.Index("ix_record_versions_entity", "entity", "entity_id"),)

    id = db.Column(db.Integer, primary_key=True)
    entity = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    patient_id = db.Column(db.Integer, index=True)
    hospital_id = db.Column(db.Integer)
    version_no = db.Column(db.Integer, nullable=False, default=1)
    action = db.Column(db.String(10), nullable=False)          # create / update / delete
    changes = db.Column(db.Text)                               # JSON {field: [old, new]}
    snapshot = db.Column(db.Text)                              # JSON state after the change
    is_amendment = db.Column(db.Boolean, default=False)        # changed AFTER the record was finalised
    amendment_reason = db.Column(db.String(300))
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    changed_at = db.Column(db.DateTime, default=now, nullable=False)

    changed_by = db.relationship("User")


class OrgSecurityPolicy(db.Model):
    __tablename__ = "org_security_policy"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), unique=True, nullable=False)
    mfa_policy = db.Column(db.String(20), default="privileged")  # privileged | all
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)


class DataProtectionRegistration(db.Model):
    """ODPC (Office of the Data Protection Commissioner) registration of a data
    controller or processor. organization_id NULL = the platform operator
    (MediCore's processor registration), managed by System Maintainers."""
    __tablename__ = "dp_registrations"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    role = db.Column(db.String(12), nullable=False)             # controller / processor
    registered_name = db.Column(db.String(200), nullable=False)
    registration_number = db.Column(db.String(60), nullable=False)
    issued_on = db.Column(db.Date)
    expires_on = db.Column(db.Date)
    evidence_reference = db.Column(db.String(300))              # where the certificate is kept (drive link, file no.)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    @property
    def is_current(self):
        return not self.expires_on or self.expires_on >= datetime.date.today()


class DpiaRecord(db.Model):
    """Data Protection Impact Assessment register entry."""
    __tablename__ = "dpia_records"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)  # NULL = platform-level
    title = db.Column(db.String(200), nullable=False)
    version = db.Column(db.String(20), default="1.0")
    scope = db.Column(db.String(500))
    risk_level = db.Column(db.String(10), default="Medium")   # Low / Medium / High
    mitigations_summary = db.Column(db.Text)
    assessor_name = db.Column(db.String(150), nullable=False)
    completed_on = db.Column(db.Date, nullable=False)
    next_review_on = db.Column(db.Date)
    evidence_reference = db.Column(db.String(300))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    @property
    def is_current(self):
        return not self.next_review_on or self.next_review_on >= datetime.date.today()


class ComplianceAttestation(db.Model):
    """Answers to compliance questions the software cannot verify by itself
    (e.g. 'Data Processor registered'). One row per (organization, key)."""
    __tablename__ = "compliance_attestations"
    __table_args__ = (db.UniqueConstraint("organization_id", "item_key", name="uq_attestation"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    item_key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(500))
    attested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    attested_at = db.Column(db.DateTime, default=now, onupdate=now)
