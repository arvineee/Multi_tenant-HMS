import datetime

from app.extensions import db


def _now():
    return datetime.datetime.utcnow()


# What a patient can consent to. Extend as new modules (sha_claims, hie) land.
CONSENT_PURPOSES = {
    "treatment": "Treatment and care within this facility",
    "sha_claims": "Sharing data with SHA for verification and claims",
    "hie": "Sharing records with the national health information exchange",
}
CONSENT_METHODS = ["written", "verbal", "digital"]

# The exact wording shown to the person when they confirm digitally. The text
# that was on screen is stored with the confirmation (and hashed), so a later
# edit here never changes what someone was recorded as agreeing to.
CONSENT_STATEMENTS = {
    "treatment": ("I agree to be examined, treated and cared for at this facility, and that "
                  "my health information may be used by the staff looking after me."),
    "sha_claims": ("I agree that this facility may share my personal and visit information "
                   "with the Social Health Authority (SHA) to verify my cover and process claims."),
    "hie": ("I agree that this facility may share my health records with the national "
            "health information exchange so other facilities I visit can see them."),
}
WITHDRAWAL_STATEMENT = "I do NOT agree, or I withdraw my earlier agreement, for the following: "


class PatientConsent(db.Model):
    """One row per consent decision. Rows are never edited: withdrawing or
    re-granting adds a new row, so the full history stays reconstructable.
    The latest row per (patient, purpose) is the current position."""
    __tablename__ = "patient_consents"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    purpose = db.Column(db.String(30), nullable=False)
    granted = db.Column(db.Boolean, nullable=False)  # False = withdrawn/refused
    method = db.Column(db.String(20), default="written")
    given_by_authorised_person_id = db.Column(
        db.Integer, db.ForeignKey("authorised_persons.id"), nullable=True
    )  # set when someone acts for the patient (minor, incapacitated)
    notes = db.Column(db.String(255))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)

    patient = db.relationship("Patient")
    recorded_by = db.relationship("User")
    authorised_person = db.relationship("AuthorisedPerson")


class AuthorisedPerson(db.Model):
    """Someone allowed to make decisions or receive information for a
    patient (parent/guardian, next of kin, legal representative)."""
    __tablename__ = "authorised_persons"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    relationship = db.Column(db.String(50), nullable=False)
    national_id = db.Column(db.String(30))
    phone = db.Column(db.String(30))
    scope = db.Column(db.String(20), default="consent")  # consent / information / both
    valid_until = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, default=_now)

    patient = db.relationship("Patient")
    recorded_by = db.relationship("User")

    @property
    def is_valid(self):
        if not self.is_active:
            return False
        return not self.valid_until or self.valid_until >= datetime.date.today()


class DigitalConsentEvidence(db.Model):
    """What the person actually saw and did when they confirmed on screen.
    One row per digital PatientConsent. Never edited."""
    __tablename__ = "consent_digital_evidence"

    id = db.Column(db.Integer, primary_key=True)
    consent_id = db.Column(db.Integer, db.ForeignKey("patient_consents.id"),
                           nullable=False, unique=True, index=True)
    signer_name = db.Column(db.String(150), nullable=False)   # typed by the person confirming
    signer_role = db.Column(db.String(60), nullable=False)    # "patient" or e.g. "Father (authorised person)"
    statement_text = db.Column(db.Text, nullable=False)       # exact wording displayed
    statement_sha256 = db.Column(db.String(64), nullable=False)
    signature_png = db.Column(db.Text)                        # optional drawn signature (PNG data URL)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    confirmed_at = db.Column(db.DateTime, default=_now, nullable=False)

    consent = db.relationship("PatientConsent", backref=db.backref("digital_evidence", uselist=False))
