"""Structured clinical record: problem list, allergies, medication list, family
history, growth, allied-health referrals, maternal & child health, immunization.

These are the persistent, patient-level records (as opposed to per-visit notes)
that DHA expects an EMR to keep. Every class here is version-tracked by
app/security/versioning.py (field-level history + amendment reasons).
"""
import datetime

from app.extensions import db
from app.models import now

PROBLEM_STATUSES = ["Active", "Resolved", "Inactive", "Ruled Out"]
ALLERGEN_TYPES = ["Drug", "Food", "Environmental", "Other"]
ALLERGY_SEVERITIES = ["Mild", "Moderate", "Severe", "Life-threatening"]
ALLERGY_STATUSES = ["Active", "Inactive", "Resolved", "Entered in error"]
ALLERGY_STATUS_OPTIONS = ["Unknown", "No known allergies", "Has allergies"]
MED_STATUSES = ["Active", "Completed", "Stopped", "On hold"]
FAMILY_RELATIONS = ["Mother", "Father", "Sibling", "Child", "Grandparent", "Aunt/Uncle", "Other"]
ALLIED_DISCIPLINES = ["Physiotherapy", "Occupational Therapy", "Nutrition/Dietetics", "Social Work", "Counselling"]
ALLIED_STATUSES = ["Referred", "Accepted", "In Progress", "Completed", "Cancelled"]
MCH_TYPES = ["ANC", "PNC", "Child Welfare", "Family Planning", "Delivery"]


class PatientProblem(db.Model):
    """Persistent problem list. A consultation diagnosis can be promoted here;
    problems also carry status over time (Active -> Resolved)."""
    __tablename__ = "patient_problems"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    diagnosis_code_id = db.Column(db.Integer, db.ForeignKey("diagnosis_codes.id"))
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="Active", nullable=False)
    is_chronic = db.Column(db.Boolean, default=False)
    onset_date = db.Column(db.Date)
    resolved_date = db.Column(db.Date)
    notes = db.Column(db.String(500))
    source = db.Column(db.String(20), default="Clinician")  # Clinician / Consultation / HIE
    source_consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    patient = db.relationship("Patient", backref=db.backref("problems", lazy=True))
    diagnosis_code = db.relationship("DiagnosisCode")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def code_display(self):
        return self.diagnosis_code.code if self.diagnosis_code else None


class PatientAllergy(db.Model):
    __tablename__ = "patient_allergies"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    allergen_type = db.Column(db.String(20), default="Drug")
    allergen_name = db.Column(db.String(150), nullable=False)
    drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"))       # link to formulary
    hpt_code = db.Column(db.String(50))                               # HPT registry code of the product/substance
    snomed_ct_code = db.Column(db.String(20))
    reaction = db.Column(db.String(200))
    severity = db.Column(db.String(20), default="Moderate")
    status = db.Column(db.String(20), default="Active")
    onset_date = db.Column(db.Date)
    notes = db.Column(db.String(300))
    source = db.Column(db.String(20), default="Clinician")           # Clinician / Legacy / HIE / Patient reported
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    patient = db.relationship("Patient", backref=db.backref("allergy_list", lazy=True))
    drug = db.relationship("Drug")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])


class PatientMedication(db.Model):
    """The patient's medication list: what they are (or were) taking. Rows are
    created when a prescription is written here, or entered by a clinician for
    medicines started elsewhere / bought over the counter."""
    __tablename__ = "patient_medications"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"))
    medication_name = db.Column(db.String(200), nullable=False)
    hpt_code = db.Column(db.String(50))
    dosage = db.Column(db.String(60))
    frequency = db.Column(db.String(60))
    route = db.Column(db.String(30))
    indication = db.Column(db.String(200))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="Active")
    stop_reason = db.Column(db.String(200))
    source = db.Column(db.String(30), default="Prescribed here")     # Prescribed here / Patient reported / HIE / Other facility
    prescription_item_id = db.Column(db.Integer, db.ForeignKey("prescription_items.id"))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    patient = db.relationship("Patient", backref=db.backref("medication_list", lazy=True))
    drug = db.relationship("Drug")
    prescription_item = db.relationship("PrescriptionItem")

    @property
    def display_name(self):
        item = self.prescription_item
        if item is not None and item.dispensed_drug is not None:
            return item.dispensed_drug.name  # what the pharmacist actually gave
        return self.medication_name

    @property
    def effective_status(self):
        """Active rows past their end date read as Completed without a write."""
        if self.status == "Active" and self.end_date and self.end_date < datetime.date.today():
            return "Completed"
        return self.status


class FamilyHistory(db.Model):
    __tablename__ = "family_history"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    relationship_to_patient = db.Column(db.String(30), nullable=False)
    condition = db.Column(db.String(200), nullable=False)
    diagnosis_code_id = db.Column(db.Integer, db.ForeignKey("diagnosis_codes.id"))
    age_at_onset = db.Column(db.Integer)
    deceased = db.Column(db.Boolean, default=False)
    notes = db.Column(db.String(300))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    patient = db.relationship("Patient", backref=db.backref("family_history", lazy=True))
    diagnosis_code = db.relationship("DiagnosisCode")


class GrowthMeasurement(db.Model):
    """Anthropometry over time. Created automatically from triage vitals and
    can be entered manually (well-baby clinic, nutrition follow-up)."""
    __tablename__ = "growth_measurements"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    triage_id = db.Column(db.Integer, db.ForeignKey("triage_records.id"), unique=True)
    measured_on = db.Column(db.Date, nullable=False, default=datetime.date.today)
    age_days = db.Column(db.Integer)                 # age at measurement, computed from DOB
    weight_kg = db.Column(db.Float)
    height_cm = db.Column(db.Float)                  # length if measured lying (<2 years)
    head_circumference_cm = db.Column(db.Float)
    muac_cm = db.Column(db.Float)
    bmi = db.Column(db.Float)
    # z-scores against the WHO reference (filled only when reference data is loaded)
    waz = db.Column(db.Float)   # weight-for-age
    haz = db.Column(db.Float)   # height/length-for-age
    baz = db.Column(db.Float)   # BMI-for-age
    whz = db.Column(db.Float)   # weight-for-length/height
    hcz = db.Column(db.Float)   # head-circumference-for-age
    source = db.Column(db.String(20), default="Manual")  # Triage / Manual
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    patient = db.relationship("Patient", backref=db.backref("growth", lazy=True, order_by="GrowthMeasurement.measured_on"))


class GrowthReference(db.Model):
    """WHO growth-standard LMS parameters, loaded with import_growth_reference.py.
    Not shipped pre-filled: the tables are WHO's published data and must be
    imported from the source files rather than typed in from memory."""
    __tablename__ = "growth_reference"
    __table_args__ = (db.UniqueConstraint("indicator", "sex", "x", name="uq_growth_ref"),)

    id = db.Column(db.Integer, primary_key=True)
    indicator = db.Column(db.String(6), nullable=False)  # wfa, lhfa, bfa, wfl, wfh, hcfa
    sex = db.Column(db.String(1), nullable=False)        # M / F
    x = db.Column(db.Float, nullable=False)              # age in days, or length/height in cm for wfl/wfh
    L = db.Column(db.Float, nullable=False)
    M = db.Column(db.Float, nullable=False)
    S = db.Column(db.Float, nullable=False)


class AlliedHealthReferral(db.Model):
    """Orders/referrals for physiotherapy, occupational therapy, nutrition &
    dietetics, social work and counselling (CPOE for non-drug services)."""
    __tablename__ = "allied_health_referrals"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    discipline = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    priority = db.Column(db.String(10), default="Routine")     # Routine / Urgent
    status = db.Column(db.String(20), default="Referred")
    goals = db.Column(db.Text)
    is_confidential = db.Column(db.Boolean, default=False)      # counselling/social work notes
    fee = db.Column(db.Numeric(12, 2), default=0)               # optional charge per session (billed when set)
    referred_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)
    completed_at = db.Column(db.DateTime)

    patient = db.relationship("Patient", backref=db.backref("allied_referrals", lazy=True))
    referred_by = db.relationship("User", foreign_keys=[referred_by_id])
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    sessions = db.relationship("AlliedHealthSession", backref="referral", lazy=True,
                               order_by="AlliedHealthSession.session_date")


class AlliedHealthSession(db.Model):
    __tablename__ = "allied_health_sessions"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    referral_id = db.Column(db.Integer, db.ForeignKey("allied_health_referrals.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    session_date = db.Column(db.Date, default=datetime.date.today)
    subjective = db.Column(db.Text)
    objective = db.Column(db.Text)
    assessment = db.Column(db.Text)
    plan = db.Column(db.Text)
    fee = db.Column(db.Numeric(12, 2), default=0)
    provider_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    provider = db.relationship("User")


class MchEncounter(db.Model):
    """Maternal & child health encounter. Common columns are real columns;
    the type-specific clinical details live in `data` (validated against
    MCH_SCHEMAS in app/records/mch.py)."""
    __tablename__ = "mch_encounters"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    encounter_type = db.Column(db.String(20), nullable=False)
    encounter_date = db.Column(db.Date, default=datetime.date.today)
    data = db.Column(db.Text, default="{}")                      # JSON
    next_visit_date = db.Column(db.Date)
    notes = db.Column(db.String(500))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    patient = db.relationship("Patient", backref=db.backref("mch_encounters", lazy=True))
    recorded_by = db.relationship("User")


class ImmunizationRecord(db.Model):
    __tablename__ = "immunizations"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    vaccine = db.Column(db.String(60), nullable=False)
    dose_number = db.Column(db.Integer)
    date_given = db.Column(db.Date, nullable=False)
    batch_number = db.Column(db.String(40))
    site = db.Column(db.String(30))
    next_due_date = db.Column(db.Date)
    given_elsewhere = db.Column(db.Boolean, default=False)      # transcribed from a card/other facility
    notes = db.Column(db.String(200))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    patient = db.relationship("Patient", backref=db.backref("immunizations", lazy=True, order_by="ImmunizationRecord.date_given"))
