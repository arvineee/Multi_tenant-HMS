import datetime
import json
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import validates

from app.extensions import db


def now():
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# Organization / Hospital
# ---------------------------------------------------------------------------

class Organization(db.Model):
    """Top-level owner. A CEO sits at this level and can see every hospital
    (branch) that belongs to their organization."""
    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=now)

    # --- platform subscription (billing the organization for USING this
    # system — separate from the Bill/Payment models above, which are the
    # organization's own patient billing) ---
    plan_level = db.Column(db.String(20), default="Level 4")  # drives price — see SUBSCRIPTION_PRICING / ONE_TIME_PRICING
    subscription_status = db.Column(db.String(20), default="trial")  # trial, active, purchased, past_due, cancelled
    trial_ends_at = db.Column(db.DateTime)
    current_period_end = db.Column(db.DateTime)  # paid access runs until this date (monthly plan only)

    hospitals = db.relationship("Hospital", backref="organization", lazy=True)
    users = db.relationship("User", backref="organization", lazy=True)

    @property
    def is_trial_active(self):
        return self.subscription_status == "trial" and self.trial_ends_at and now() < self.trial_ends_at

    @property
    def is_purchased(self):
        """Bought outright — a one-time payment, no recurring billing,
        no expiry to track at all."""
        return self.subscription_status == "purchased"

    @property
    def is_subscription_active(self):
        return self.subscription_status == "active" and self.current_period_end and now() < self.current_period_end

    @property
    def has_access(self):
        return self.is_trial_active or self.is_subscription_active or self.is_purchased

    @property
    def trial_days_remaining(self):
        if not self.trial_ends_at:
            return 0
        delta = self.trial_ends_at - now()
        return max(delta.days + (1 if delta.seconds > 0 else 0), 0)


HOSPITAL_LEVELS = ["Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Level 6"]


class Hospital(db.Model):
    """A single facility/branch. Every clinical & operational record is
    scoped to a hospital_id so data never leaks across facilities."""
    __tablename__ = "hospitals"
    __table_args__ = (db.UniqueConstraint("organization_id", "code", name="uq_hospital_code_per_org"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)

    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(20), nullable=False)  # short internal code, used in patient IDs — unique per organization, not globally
    level = db.Column(db.String(20), nullable=False, default="Level 4")

    address = db.Column(db.String(255))
    county = db.Column(db.String(100))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    logo_path = db.Column(db.String(255))

    # facility-specific settings that admins can change without touching code
    settings_json = db.Column(db.Text, default="{}")

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)

    users = db.relationship("User", backref="hospital", lazy=True)

    def get_setting(self, key, default=None):
        try:
            data = json.loads(self.settings_json or "{}")
        except (TypeError, ValueError):
            data = {}
        return data.get(key, default)

    def set_setting(self, key, value):
        try:
            data = json.loads(self.settings_json or "{}")
        except (TypeError, ValueError):
            data = {}
        data[key] = value
        self.settings_json = json.dumps(data)

    @property
    def low_stock_threshold(self):
        """Reorder alert level for this hospital's pharmacy — falls back to
        the system default (config.py: LOW_STOCK_THRESHOLD_DEFAULT) until
        the owner sets one via the Pricing & System Settings page."""
        value = self.get_setting("low_stock_threshold")
        if value is None:
            try:
                from flask import current_app
                return current_app.config.get("LOW_STOCK_THRESHOLD_DEFAULT", LOW_STOCK_THRESHOLD)
            except RuntimeError:
                return LOW_STOCK_THRESHOLD  # no app context (e.g. called from a standalone script)
        try:
            return int(value)
        except (TypeError, ValueError):
            return LOW_STOCK_THRESHOLD


# ---------------------------------------------------------------------------
# Roles & Permissions (RBAC)
# ---------------------------------------------------------------------------

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)  # e.g. 'pharmacy.dispense'
    module = db.Column(db.String(50), nullable=False)  # e.g. 'pharmacy'
    description = db.Column(db.String(200))


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))

    # scope determines what a user with this role is allowed to "see"
    # 'organization' = all hospitals in the org (CEO)
    # 'hospital'      = only their assigned hospital (Hospital Manager, staff)
    # 'department'    = only their assigned hospital + their own records (default clinical staff)
    scope = db.Column(db.String(20), default="department")

    is_system = db.Column(db.Boolean, default=True)  # system roles can't be deleted from UI

    permissions = db.relationship(
        "Permission", secondary=role_permissions, backref="roles", lazy="subquery"
    )
    users = db.relationship("User", backref="role", lazy=True)

    def has_permission(self, code):
        return any(p.code == code for p in self.permissions)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=True)  # null for CEO
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)

    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30))
    department = db.Column(db.String(80))

    is_active = db.Column(db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def has_permission(self, code):
        return self.role.has_permission(code) if self.role else False

    def accessible_hospital_ids(self):
        """Returns the list of hospital IDs this user is allowed to view."""
        if self.role.scope == "platform":
            # System Maintainer — every hospital, across every
            # organization. This is the entire access-control basis for
            # "sees everything": every hospital-scoped query in the app
            # (patients, billing, pharmacy, inpatient, wards...) already
            # filters through this method, so widening it here is what
            # actually grants the cross-tenant visibility, rather than
            # needing every route to special-case this role individually.
            return [h.id for h in Hospital.query.all()]
        if self.role.scope == "organization":
            return [h.id for h in Hospital.query.filter_by(organization_id=self.organization_id).all()]
        extra = [uha.hospital_id for uha in self.extra_hospital_access]
        ids = set(extra)
        if self.hospital_id:
            ids.add(self.hospital_id)
        return list(ids)

    def __repr__(self):
        return f"<User {self.username} ({self.role.name if self.role else '-'})>"


class UserHospitalAccess(db.Model):
    """Lets a Hospital Manager who oversees more than one branch see all of
    them, without granting full organization-wide (CEO) scope."""
    __tablename__ = "user_hospital_access"
    __table_args__ = (db.UniqueConstraint("user_id", "hospital_id", name="uq_user_hospital_access"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)

    user = db.relationship("User", backref="extra_hospital_access")
    hospital = db.relationship("Hospital")

    @validates("hospital_id", "user_id")
    def _validate_same_organization(self, key, value):
        """Belt-and-braces check: a grant can never point a user at a
        hospital outside their own organization, even if a caller forgets
        to check this before inserting. Only fires once both sides of the
        relationship are resolvable (e.g. on flush), so it won't false-fire
        on partially-built objects during construction."""
        hospital_id = value if key == "hospital_id" else self.hospital_id
        user_id = value if key == "user_id" else self.user_id
        if hospital_id is not None and user_id is not None:
            hospital = db.session.get(Hospital, hospital_id)
            user = db.session.get(User, user_id)
            if hospital and user and hospital.organization_id != user.organization_id:
                raise ValueError(
                    "Cannot grant hospital access outside the user's own organization."
                )
        return value


# ---------------------------------------------------------------------------
# Audit log — required for medical-legal traceability
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=True)

    action = db.Column(db.String(50), nullable=False)  # create/update/delete/login/dispense...
    model_name = db.Column(db.String(80))
    record_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, default=now)

    user = db.relationship("User")
    hospital = db.relationship("Hospital")


def log_action(user, action, model_name=None, record_id=None, details=None, ip_address=None):
    """Writes an audit row into the *current* session without committing it
    separately. Callers should call this before their own db.session.commit()
    so the audit entry lands in the exact same transaction as the change it
    describes — either both are saved, or (if something goes wrong) neither
    is, instead of the change succeeding silently with no audit trail.

    ip_address is pulled automatically from the active Flask request if the
    caller doesn't supply one, so call sites don't have to remember to pass
    it (previously only login/logout did).

    Never raises: a malformed 'details' payload or missing request context
    should not be able to block the actual clinical/billing operation this
    call is auditing. Failures are logged instead.
    """
    if ip_address is None:
        try:
            from flask import request, has_request_context
            if has_request_context():
                ip_address = request.remote_addr
        except RuntimeError:
            ip_address = None

    try:
        if isinstance(details, (dict, list)):
            details_str = json.dumps(details, default=str)
        else:
            details_str = details
    except TypeError:
        details_str = str(details)

    try:
        entry = AuditLog(
            user_id=user.id if user and getattr(user, "is_authenticated", False) else None,
            hospital_id=getattr(user, "hospital_id", None),
            action=action,
            model_name=model_name,
            record_id=record_id,
            details=details_str,
            ip_address=ip_address,
        )
        db.session.add(entry)
    except Exception:
        import logging
        logging.getLogger("audit").exception(
            "Failed to queue audit log entry: action=%s model=%s record_id=%s",
            action, model_name, record_id,
        )


# ---------------------------------------------------------------------------
# Master catalogs (admin-managed, shared across the whole system)
# ---------------------------------------------------------------------------

class DiagnosisCode(db.Model):
    """ICD-10 style diagnosis catalog used by the consultation dropdown."""
    __tablename__ = "diagnosis_codes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)


class Drug(db.Model):
    """Formulary used by the prescription dropdown. Scoped per organization
    so unrelated hospital groups manage entirely separate formularies."""
    __tablename__ = "drugs"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    generic_name = db.Column(db.String(150))
    form = db.Column(db.String(50))       # tablet, syrup, injection...
    strength = db.Column(db.String(50))   # 500mg, 250mg/5ml...
    category = db.Column(db.String(100))  # antibiotic, analgesic...
    is_active = db.Column(db.Boolean, default=True)
    # Protocol drugs a nurse can give for symptomatic relief at triage,
    # before the doctor has seen the patient (paracetamol for fever, ORS,
    # a first-line antihistamine, etc.) — deliberately opt-in per drug
    # rather than a blanket permission, so what nurses can give without a
    # doctor's order stays a short, hospital-chosen list.
    is_triage_relief = db.Column(db.Boolean, default=False)


class RadiologyTest(db.Model):
    """Imaging catalog used by the radiology order dropdown. Scoped per
    organization — one hospital group's custom test doesn't leak into
    another's."""
    __tablename__ = "radiology_tests"
    __table_args__ = (db.UniqueConstraint("organization_id", "code", name="uq_radiology_code_per_org"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    modality = db.Column(db.String(50))  # X-Ray, Ultrasound, CT, MRI
    price = db.Column(db.Numeric(12, 2), default=0)
    is_active = db.Column(db.Boolean, default=True)


class LabTest(db.Model):
    """Lab test catalog used by the lab order dropdown. Scoped per
    organization."""
    __tablename__ = "lab_tests"
    __table_args__ = (db.UniqueConstraint("organization_id", "code", name="uq_labtest_code_per_org"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(50))  # Hematology, Chemistry, Microbiology...
    price = db.Column(db.Numeric(12, 2), default=0)
    is_active = db.Column(db.Boolean, default=True)


class InsuranceScheme(db.Model):
    """Insurance/NHIF-SHA schemes used at billing time. Scoped per
    organization — each hospital group contracts its own set of schemes."""
    __tablename__ = "insurance_schemes"
    __table_args__ = (db.UniqueConstraint("organization_id", "code", name="uq_insurance_code_per_org"),)

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(30), nullable=False)
    scheme_type = db.Column(db.String(50))  # NHIF/SHA, Private, Corporate
    is_active = db.Column(db.Boolean, default=True)
    # Default pre-authorization ceiling used to seed Admission.insurance_limit
    # when a scheme member is admitted, if no per-admission limit is entered
    # explicitly by billing staff (e.g. from an insurer's authorization letter).
    default_credit_limit = db.Column(db.Numeric(12, 2), nullable=True)


# ---------------------------------------------------------------------------
# Patients & Visits
# ---------------------------------------------------------------------------

GENDERS = ["Male", "Female"]


class Patient(db.Model):
    """One bio-data record per person, scoped to the hospital where they
    were first registered. patient_number is unique per hospital and is
    what staff use to look someone up at the desk."""
    __tablename__ = "patients"
    __table_args__ = (db.UniqueConstraint("hospital_id", "patient_number", name="uq_patient_number_per_hospital"),)

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    patient_number = db.Column(db.String(30), nullable=False)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(10))
    date_of_birth = db.Column(db.Date)
    # Fallback for when the exact date of birth isn't known (common at
    # walk-in registration). Stored alongside the date it was recorded so
    # `age` can still be derived correctly even if the patient is looked up
    # years later.
    estimated_age_years = db.Column(db.Integer)
    estimated_age_recorded_on = db.Column(db.Date)
    national_id = db.Column(db.String(30))
    phone = db.Column(db.String(30))
    address = db.Column(db.String(255))
    blood_group = db.Column(db.String(5))

    next_of_kin_name = db.Column(db.String(150))
    next_of_kin_phone = db.Column(db.String(30))
    next_of_kin_relationship = db.Column(db.String(50))

    allergies = db.Column(db.String(500))
    chronic_conditions = db.Column(db.String(500))

    insurance_scheme_id = db.Column(db.Integer, db.ForeignKey("insurance_schemes.id"), nullable=True)
    insurance_member_number = db.Column(db.String(50))

    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    hospital = db.relationship("Hospital")
    insurance_scheme = db.relationship("InsuranceScheme")
    created_by = db.relationship("User")
    visits = db.relationship("Visit", backref="patient", lazy=True, order_by="Visit.created_at.desc()")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        today = datetime.date.today()
        if self.date_of_birth:
            years = today.year - self.date_of_birth.year
            if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
                years -= 1
            return years
        if self.estimated_age_years is not None:
            # Roll the recorded estimate forward by however many years have
            # passed since it was taken, so it doesn't go stale.
            recorded_on = self.estimated_age_recorded_on or self.created_at.date() if self.created_at else today
            elapsed_years = today.year - recorded_on.year
            if (today.month, today.day) < (recorded_on.month, recorded_on.day):
                elapsed_years -= 1
            return self.estimated_age_years + max(elapsed_years, 0)
        return None

    @property
    def age_is_estimated(self):
        return not self.date_of_birth and self.estimated_age_years is not None

    @property
    def latest_visit(self):
        return self.visits[0] if self.visits else None

    @property
    def is_archived(self):
        """A patient drops out of the default (active) list once their
        most recent visit has closed out (Completed/Discharged) and
        nothing else is open — they move to the archive. Checking them in
        again (a new Visit row, whether a walk-in revisit or a fresh
        admission) makes that the new latest visit, so they reappear in
        the active list automatically — no flag to remember to flip."""
        v = self.latest_visit
        return bool(v) and v.status in ("Completed", "Discharged")


VISIT_TYPES = ["Outpatient", "Inpatient"]
VISIT_STATUSES = ["Waiting", "Triaged", "In Consultation", "Completed", "Admitted", "Discharged"]


class Visit(db.Model):
    """One check-in / encounter. Everything clinical for that encounter
    (consultation, prescriptions, lab, radiology, billing) will attach to
    this record once those modules are built."""
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    visit_type = db.Column(db.String(20), nullable=False, default="Outpatient")
    status = db.Column(db.String(20), nullable=False, default="Waiting")
    reason = db.Column(db.String(255))

    checked_in_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)
    closed_at = db.Column(db.DateTime)

    checked_in_by = db.relationship("User")
    hospital = db.relationship("Hospital")
    triage = db.relationship("Triage", backref="visit", uselist=False)
    consultation = db.relationship("Consultation", backref="visit", uselist=False)
    admission = db.relationship("Admission", backref="visit", uselist=False)
    lab_orders = db.relationship("LabOrder", backref="visit", lazy=True, order_by="LabOrder.ordered_at.desc()")
    radiology_orders = db.relationship("RadiologyOrder", backref="visit", lazy=True, order_by="RadiologyOrder.ordered_at.desc()")
    prescriptions = db.relationship("Prescription", backref="visit", lazy=True, order_by="Prescription.created_at.desc()")
    bill = db.relationship("Bill", backref="visit", uselist=False)
    documents = db.relationship("MedicalDocument", backref="visit", lazy=True, order_by="MedicalDocument.issued_at.desc()")

    @property
    def current_stage(self):
        """Human-readable 'what's next' for this visit — shown on the queue
        and patient page so staff don't have to interpret raw status codes."""
        if self.status == "Waiting":
            return "Awaiting triage"
        if self.status == "Triaged":
            return "Awaiting doctor"
        if self.status == "In Consultation":
            pending_lab = any(o.status not in ("Result Ready", "Cancelled") for o in self.lab_orders)
            pending_rad = any(o.status not in ("Report Ready", "Cancelled") for o in self.radiology_orders)
            pending_rx = any(p.status in ("Pending", "Partially Dispensed") for p in self.prescriptions)
            waiting_on = []
            if pending_lab:
                waiting_on.append("lab results")
            if pending_rad:
                waiting_on.append("radiology results")
            if pending_rx:
                waiting_on.append("pharmacy")
            if waiting_on:
                return "Awaiting " + " & ".join(waiting_on)
            if self.consultation and not self.consultation.diagnosis_code_id:
                return "Awaiting diagnosis"
            return "With doctor"
        if self.status == "Admitted":
            return "Inpatient — admitted"
        if self.status == "Completed":
            return "Awaiting billing" if not self.bill else ("Awaiting payment" if self.bill.balance_due > 0 else "Fully settled")
        if self.status == "Discharged":
            return "Awaiting billing" if not self.bill else ("Awaiting payment" if self.bill.balance_due > 0 else "Fully settled")
        return self.status

    @property
    def has_pending_results(self):
        pending_lab = any(o.status not in ("Result Ready", "Cancelled") for o in self.lab_orders)
        pending_rad = any(o.status not in ("Report Ready", "Cancelled") for o in self.radiology_orders)
        pending_rx = any(p.status in ("Pending", "Partially Dispensed") for p in self.prescriptions)
        return pending_lab or pending_rad or pending_rx


# ---------------------------------------------------------------------------
# Triage — vitals captured before a doctor sees the patient
# ---------------------------------------------------------------------------

TRIAGE_PRIORITIES = ["Emergency", "Urgent", "Normal"]


class Triage(db.Model):
    __tablename__ = "triage_records"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    temperature_c = db.Column(db.Float)
    pulse_bpm = db.Column(db.Integer)
    bp_systolic = db.Column(db.Integer)
    bp_diastolic = db.Column(db.Integer)
    respiratory_rate = db.Column(db.Integer)
    spo2_percent = db.Column(db.Integer)
    weight_kg = db.Column(db.Float)
    height_cm = db.Column(db.Float)

    priority = db.Column(db.String(20), default="Normal")
    notes = db.Column(db.String(500))

    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    recorded_by = db.relationship("User")
    patient = db.relationship("Patient")

    @property
    def bmi(self):
        if not self.weight_kg or not self.height_cm:
            return None
        height_m = self.height_cm / 100
        return round(self.weight_kg / (height_m ** 2), 1)


# ---------------------------------------------------------------------------
# Consultation — doctor's assessment, diagnosis, treatment plan
# ---------------------------------------------------------------------------

class Consultation(db.Model):
    __tablename__ = "consultations"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # --- History taking (standard clerking structure) ---
    chief_complaint = db.Column(db.String(500))
    history_of_presenting_illness = db.Column(db.Text)
    past_medical_history = db.Column(db.Text)
    past_surgical_history = db.Column(db.Text)
    drug_history = db.Column(db.Text)           # current medications, incl. herbal/OTC
    family_social_history = db.Column(db.Text)
    review_of_systems = db.Column(db.Text)

    # --- Examination ---
    examination_notes = db.Column(db.Text)       # general + systemic findings

    # --- Assessment & plan ---
    diagnosis_code_id = db.Column(db.Integer, db.ForeignKey("diagnosis_codes.id"))  # primary diagnosis
    diagnosis_notes = db.Column(db.String(500))
    treatment_plan = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=now)

    doctor = db.relationship("User")
    patient = db.relationship("Patient")
    diagnosis_code = db.relationship("DiagnosisCode")
    additional_diagnoses = db.relationship(
        "ConsultationDiagnosis", backref="consultation", lazy=True,
        cascade="all, delete-orphan", order_by="ConsultationDiagnosis.id",
    )

    @property
    def all_diagnoses(self):
        """Primary diagnosis first, then every secondary/comorbid one —
        the full list a discharge summary or billing code review would
        want to see, not just the headline diagnosis."""
        result = []
        if self.diagnosis_code:
            result.append(self.diagnosis_code)
        result.extend(d.diagnosis_code for d in self.additional_diagnoses if d.diagnosis_code)
        return result


class ConsultationDiagnosis(db.Model):
    """A secondary/comorbid diagnosis on a consultation — the primary
    diagnosis stays on Consultation.diagnosis_code_id itself (required to
    finalize a consultation), this table is everything *in addition to*
    that. E.g. a patient admitted for malaria who also has known
    hypertension: primary = malaria, one row here = hypertension."""
    __tablename__ = "consultation_diagnoses"

    id = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"), nullable=False)
    diagnosis_code_id = db.Column(db.Integer, db.ForeignKey("diagnosis_codes.id"), nullable=False)
    notes = db.Column(db.String(300))

    diagnosis_code = db.relationship("DiagnosisCode")


# ---------------------------------------------------------------------------
# Lab & Radiology orders — placed mid-consultation, resolved independently
# so the doctor isn't blocked waiting on results before seeing another patient
# ---------------------------------------------------------------------------

LAB_ORDER_STATUSES = ["Ordered", "Sample Collected", "Result Ready", "Cancelled"]
RADIOLOGY_ORDER_STATUSES = ["Ordered", "In Progress", "Report Ready", "Cancelled"]


class LabOrder(db.Model):
    __tablename__ = "lab_orders"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    lab_test_id = db.Column(db.Integer, db.ForeignKey("lab_tests.id"), nullable=False)

    status = db.Column(db.String(20), default="Ordered")
    clinical_notes = db.Column(db.String(300))  # from the doctor, why this test is needed
    result_value = db.Column(db.Text)
    result_notes = db.Column(db.Text)

    ordered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resulted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    ordered_at = db.Column(db.DateTime, default=now)
    resulted_at = db.Column(db.DateTime)

    lab_test = db.relationship("LabTest")
    patient = db.relationship("Patient")
    ordered_by = db.relationship("User", foreign_keys=[ordered_by_id])
    resulted_by = db.relationship("User", foreign_keys=[resulted_by_id])


class RadiologyOrder(db.Model):
    __tablename__ = "radiology_orders"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    radiology_test_id = db.Column(db.Integer, db.ForeignKey("radiology_tests.id"), nullable=False)

    status = db.Column(db.String(20), default="Ordered")
    clinical_notes = db.Column(db.String(300))
    findings = db.Column(db.Text)

    ordered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reported_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    ordered_at = db.Column(db.DateTime, default=now)
    reported_at = db.Column(db.DateTime)

    radiology_test = db.relationship("RadiologyTest")
    patient = db.relationship("Patient")
    ordered_by = db.relationship("User", foreign_keys=[ordered_by_id])
    reported_by = db.relationship("User", foreign_keys=[reported_by_id])


# ---------------------------------------------------------------------------
# Wards & Inpatient Admission
# ---------------------------------------------------------------------------

class Ward(db.Model):
    """Ward catalog per hospital. Bed-level occupancy is tracked via the
    Bed model below when beds have been individually set up; total_beds
    stays as the headline capacity number either way."""
    __tablename__ = "wards"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    ward_type = db.Column(db.String(50))  # General, Maternity, ICU, Pediatric, Surgical...
    total_beds = db.Column(db.Integer, default=0)
    daily_rate = db.Column(db.Numeric(12, 2), default=0)
    is_active = db.Column(db.Boolean, default=True)

    hospital = db.relationship("Hospital")
    beds = db.relationship("Bed", backref="ward", lazy=True, order_by="Bed.label")

    @property
    def occupied_beds(self):
        if self.beds:
            return sum(1 for b in self.beds if b.status == "Occupied")
        return Admission.query.filter_by(ward_id=self.id, status="Active").count()

    @property
    def available_beds(self):
        if self.beds:
            return sum(1 for b in self.beds if b.status == "Available")
        return max(self.total_beds - self.occupied_beds, 0)


BED_STATUSES = ["Available", "Occupied", "Maintenance"]


class Bed(db.Model):
    """An individually tracked bed within a ward. Optional — a ward can
    operate on just a headline total_beds count if its beds were never
    set up individually here, in which case Admission.bed_number stays a
    free-text label instead."""
    __tablename__ = "beds"
    __table_args__ = (db.UniqueConstraint("ward_id", "label", name="uq_bed_label_per_ward"),)

    id = db.Column(db.Integer, primary_key=True)
    ward_id = db.Column(db.Integer, db.ForeignKey("wards.id"), nullable=False)
    label = db.Column(db.String(20), nullable=False)  # e.g. "Bed 1", "ICU-02"
    status = db.Column(db.String(20), default="Available")


class Admission(db.Model):
    """One inpatient stay. Created when a consultation admits a patient,
    closed when they're discharged. Can move between wards/beds mid-stay
    via WardTransfer without losing continuity of the stay itself."""
    __tablename__ = "admissions"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    ward_id = db.Column(db.Integer, db.ForeignKey("wards.id"), nullable=False)
    bed_id = db.Column(db.Integer, db.ForeignKey("beds.id"), nullable=True)

    bed_number = db.Column(db.String(20))  # free-text fallback when the ward has no individually tracked beds
    admitting_doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    admission_date = db.Column(db.DateTime, default=now)
    expected_discharge_date = db.Column(db.Date)
    actual_discharge_date = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="Active")  # Active, Discharged
    notes = db.Column(db.String(500))

    # --- admitting diagnosis, carried over from the OPD/triage consultation
    # that led to this admission, so the inpatient record doesn't need the
    # doctor to re-type a clerking that already happened. Null when the
    # admission was opened straight into an Inpatient visit with no prior
    # outpatient consultation. ---
    admitting_consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"), nullable=True)

    # --- insurance authorization tracking for this stay. Left null for a
    # Cash patient (no limit is ever enforced there). For an insured
    # patient, billing staff record what the insurer has pre-authorized;
    # accumulated inpatient charges are compared against it as items are
    # ordered, and insurance_limit_reached flips to True once the running
    # total meets/exceeds it so staff can escalate for a top-up. ---
    insurance_authorized_amount = db.Column(db.Numeric(12, 2), nullable=True)
    insurance_limit_reached = db.Column(db.Boolean, default=False)

    # --- discharge ---
    discharge_summary_document_id = db.Column(db.Integer, db.ForeignKey("medical_documents.id"), nullable=True)

    patient = db.relationship("Patient")
    ward = db.relationship("Ward")
    bed = db.relationship("Bed")
    admitting_doctor = db.relationship("User")
    admitting_consultation = db.relationship("Consultation", foreign_keys=[admitting_consultation_id])
    discharge_summary_document = db.relationship("MedicalDocument", foreign_keys=[discharge_summary_document_id])
    vitals = db.relationship("InpatientVitals", backref="admission", lazy=True, order_by="InpatientVitals.recorded_at.desc()")
    nursing_notes = db.relationship("NursingNote", backref="admission", lazy=True, order_by="NursingNote.created_at.desc()")
    transfers = db.relationship("WardTransfer", backref="admission", lazy=True, order_by="WardTransfer.transferred_at.desc()")
    doctor_reviews = db.relationship("DoctorReview", backref="admission", lazy=True, order_by="DoctorReview.created_at.desc()")
    care_plans = db.relationship("CarePlan", backref="admission", lazy=True, order_by="CarePlan.created_at.desc()")
    monitoring_entries = db.relationship("MonitoringEntry", backref="admission", lazy=True, order_by="MonitoringEntry.recorded_at.desc()")
    procedure_orders = db.relationship("ProcedureOrder", backref="admission", lazy=True, order_by="ProcedureOrder.ordered_at.desc()")

    @property
    def bed_label(self):
        return self.bed.label if self.bed else (self.bed_number or "—")

    @property
    def length_of_stay_days(self):
        end = self.actual_discharge_date.date() if self.actual_discharge_date else datetime.date.today()
        return max((end - self.admission_date.date()).days, 1)

    @property
    def active_care_plan(self):
        return self.care_plans[0] if self.care_plans else None

    def running_bill_total(self):
        """Live accumulated charge total for this stay so far, using the
        same line-item logic billing uses to generate the final bill —
        without needing a Bill row to exist yet. Used purely to compare
        against insurance_authorized_amount as items pile up."""
        from app.billing.routes import _build_line_items
        if not self.visit:
            return 0.0
        lines = _build_line_items(type("_Stub", (), {"id": None})(), self.visit)
        return round(float(sum(float(l.amount or 0) for l in lines)), 2)

    def refresh_insurance_limit_flag(self):
        """Call after anything billable happens on this admission. No-op
        for cash patients (no scheme, no limit) — insurance authorization
        limits only ever apply to insured stays; cash always proceeds."""
        if not self.insurance_authorized_amount:
            self.insurance_limit_reached = False
            return
        self.insurance_limit_reached = self.running_bill_total() >= float(self.insurance_authorized_amount)


class InpatientVitals(db.Model):
    """Repeated nursing-round vitals over the course of a stay — distinct
    from the one-off triage vitals taken at check-in."""
    __tablename__ = "inpatient_vitals"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    temperature_c = db.Column(db.Float)
    pulse_bpm = db.Column(db.Integer)
    bp_systolic = db.Column(db.Integer)
    bp_diastolic = db.Column(db.Integer)
    respiratory_rate = db.Column(db.Integer)
    spo2_percent = db.Column(db.Integer)
    notes = db.Column(db.String(300))

    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    recorded_by = db.relationship("User")


class NursingNote(db.Model):
    """Free-text nursing/ward-round notes logged over the course of a
    stay — separate from the consultation's examination notes, which is
    a one-time doctor's assessment."""
    __tablename__ = "nursing_notes"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    note = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    author = db.relationship("User")


class WardTransfer(db.Model):
    """Log of a patient moving wards/beds mid-stay, so there's a clean
    audit trail of where they've actually been during the admission."""
    __tablename__ = "ward_transfers"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    from_ward_id = db.Column(db.Integer, db.ForeignKey("wards.id"))
    to_ward_id = db.Column(db.Integer, db.ForeignKey("wards.id"), nullable=False)
    from_bed_id = db.Column(db.Integer, db.ForeignKey("beds.id"))
    to_bed_id = db.Column(db.Integer, db.ForeignKey("beds.id"))
    reason = db.Column(db.String(300))

    transferred_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    transferred_at = db.Column(db.DateTime, default=now)

    from_ward = db.relationship("Ward", foreign_keys=[from_ward_id])
    to_ward = db.relationship("Ward", foreign_keys=[to_ward_id])
    from_bed = db.relationship("Bed", foreign_keys=[from_bed_id])
    to_bed = db.relationship("Bed", foreign_keys=[to_bed_id])
    transferred_by = db.relationship("User")


# ---------------------------------------------------------------------------
# Doctor reviews (daily ward rounds), nursing care plan/cardex, and
# monitoring charts (I/O, blood sugar, etc.) — the ongoing inpatient
# documentation that happens over a multi-day stay, distinct from the
# one-time OPD-style clerking on Consultation.
# ---------------------------------------------------------------------------

class DoctorReview(db.Model):
    """One ward-round review entry. Any doctor with access to the ward can
    add one — an admission commonly gets reviewed by more than one doctor
    over a stay (the admitting doctor, an on-call doctor, a specialist).
    This is what feeds 'daily ward round review notes' and, at discharge,
    the auto-generated hospital-course narrative."""
    __tablename__ = "doctor_reviews"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    findings = db.Column(db.Text)             # examination / progress findings on this round
    assessment = db.Column(db.Text)           # doctor's read of how the patient is progressing
    plan = db.Column(db.Text)                 # what changes as a result (meds, investigations, procedures)
    procedure_done = db.Column(db.String(300))  # e.g. "Wound dressing", "Catheter insertion" — optional

    created_at = db.Column(db.DateTime, default=now)

    doctor = db.relationship("User")


CARE_PLAN_STATUSES = ["Active", "Resolved", "Discontinued"]


class CarePlan(db.Model):
    """Nursing care plan / cardex for the stay: the problem being managed,
    the goal, and the interventions nurses are expected to carry out each
    shift. An admission can have more than one active problem at a time,
    so this is a list rather than a single free-text field."""
    __tablename__ = "care_plans"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    problem = db.Column(db.String(255), nullable=False)   # nursing diagnosis / identified problem
    goal = db.Column(db.Text)
    interventions = db.Column(db.Text)                    # what nurses should do each shift
    status = db.Column(db.String(20), default="Active")

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    created_by = db.relationship("User")


MONITORING_CHART_TYPES = ["Intake", "Output", "Blood Sugar", "Pain Score", "Other"]
MONITORING_CHART_UNITS = {
    "Intake": "ml", "Output": "ml", "Blood Sugar": "mmol/L", "Pain Score": "/10", "Other": "",
}


class MonitoringEntry(db.Model):
    """A single reading on a repeated inpatient monitoring chart — fluid
    input/output, capillary blood sugar, pain score, or any other chart a
    ward wants to track over the stay. One flexible table rather than a
    separate table per chart type, since they all share the same shape:
    a timestamped numeric reading with a route/context note."""
    __tablename__ = "monitoring_entries"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    chart_type = db.Column(db.String(30), nullable=False)  # see MONITORING_CHART_TYPES
    value = db.Column(db.Float, nullable=False)
    route_or_context = db.Column(db.String(100))  # e.g. "Oral", "IV line", "Urine catheter", "Vomitus"
    notes = db.Column(db.String(300))

    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=now)

    recorded_by = db.relationship("User")

    @property
    def unit(self):
        return MONITORING_CHART_UNITS.get(self.chart_type, "")


PROCEDURE_ORDER_STATUSES = ["Ordered", "Done", "Cancelled"]


class ProcedureOrder(db.Model):
    """A billable ward-side procedure or service performed during the
    stay — wound dressing, catheter insertion, oxygen therapy, minor
    surgery, whatever isn't already covered by pharmacy/lab/radiology.
    Only charged once actually performed (status='Done'), same billing
    philosophy as lab/radiology results — a bill should reflect what
    happened, not what was merely requested."""
    __tablename__ = "procedure_orders"

    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    notes = db.Column(db.String(300))
    quantity = db.Column(db.Numeric(10, 2), default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    status = db.Column(db.String(20), default="Ordered")

    ordered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    ordered_at = db.Column(db.DateTime, default=now)
    performed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    performed_at = db.Column(db.DateTime)

    ordered_by = db.relationship("User", foreign_keys=[ordered_by_id])
    performed_by = db.relationship("User", foreign_keys=[performed_by_id])

    @property
    def amount(self):
        # Stay in Decimal — every other BillLineItem.amount on a bill comes
        # straight from a Numeric column, and Decimal + float raises a
        # TypeError outright rather than silently coercing, so mixing one
        # float in here breaks Bill.total_amount for the whole bill.
        return (Decimal(str(self.quantity or 0)) * Decimal(str(self.unit_price or 0))).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Prescription & Pharmacy
# ---------------------------------------------------------------------------

PRESCRIPTION_ITEM_STATUSES = ["Pending", "Partially Dispensed", "Dispensed", "Out of Stock", "Cancelled"]
LOW_STOCK_THRESHOLD = 10  # fallback reorder alert level — overridable per hospital via Hospital.get_setting("low_stock_threshold")


class Prescription(db.Model):
    """One prescription per visit. Individual drugs live in PrescriptionItem
    so each can be dispensed (or not) independently — a pharmacist might
    have three of five items in stock and dispense only those."""
    __tablename__ = "prescriptions"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)  # who ordered it — a nurse for a triage relief order

    # True for a nurse-given relief dose at triage, before the doctor has
    # seen the patient — restricted at the route level to drugs flagged
    # Drug.is_triage_relief. False (the default) is an ordinary
    # doctor-ordered prescription, unchanged from before.
    is_triage_order = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=now)

    patient = db.relationship("Patient")
    doctor = db.relationship("User")
    items = db.relationship("PrescriptionItem", backref="prescription", lazy=True)

    @property
    def status(self):
        statuses = {item.status for item in self.items}
        if not statuses or statuses == {"Cancelled"}:
            return "Cancelled"
        if statuses <= {"Dispensed", "Cancelled"}:
            return "Dispensed"
        if "Dispensed" in statuses or "Partially Dispensed" in statuses:
            return "Partially Dispensed"
        return "Pending"


class PrescriptionItem(db.Model):
    __tablename__ = "prescription_items"

    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey("prescriptions.id"), nullable=False)
    drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"), nullable=False)  # as prescribed by the doctor

    dosage = db.Column(db.String(50))        # e.g. "500mg" — as prescribed
    frequency = db.Column(db.String(50))     # e.g. "TDS", "BD", "OD"
    duration = db.Column(db.String(50))      # e.g. "5 days"
    instructions = db.Column(db.String(200))  # e.g. "After meals"
    quantity_prescribed = db.Column(db.Integer, nullable=False, default=1)
    quantity_dispensed = db.Column(db.Integer, nullable=False, default=0)
    billed_amount = db.Column(db.Numeric(12, 2), default=0)  # accumulated from actual batch selling prices at dispense time
    status = db.Column(db.String(20), default="Pending")

    # --- pharmacist substitution / dosage adjustment at dispense time.
    # Left null when the item is dispensed exactly as prescribed. When the
    # pharmacist swaps in a different drug (an allergy, a known side
    # effect, the patient's already tried it without success, or it's
    # simply out of stock) or adjusts the dosage, that's recorded here —
    # the original prescription (drug_id/dosage) is never overwritten, so
    # what the doctor ordered and what the patient actually received both
    # stay on the record. ---
    dispensed_drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"), nullable=True)
    dispensed_dosage = db.Column(db.String(50), nullable=True)
    substitution_reason = db.Column(db.String(300), nullable=True)

    drug = db.relationship("Drug", foreign_keys=[drug_id])
    dispensed_drug = db.relationship("Drug", foreign_keys=[dispensed_drug_id])

    @property
    def quantity_remaining(self):
        return max(self.quantity_prescribed - self.quantity_dispensed, 0)

    @property
    def effective_drug(self):
        """What the patient actually receives — the substitute if the
        pharmacist swapped one in, otherwise the drug as prescribed."""
        return self.dispensed_drug or self.drug

    @property
    def effective_dosage(self):
        return self.dispensed_dosage or self.dosage

    @property
    def is_substituted(self):
        return self.dispensed_drug_id is not None and self.dispensed_drug_id != self.drug_id

    @property
    def is_dosage_adjusted(self):
        return bool(self.dispensed_dosage) and self.dispensed_dosage != self.dosage


class StockBatch(db.Model):
    """One received batch of a drug at a hospital. Dispensing draws from
    the earliest-expiring batch with stock left (FEFO) so nothing expires
    on the shelf while a newer batch gets used first."""
    __tablename__ = "stock_batches"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"), nullable=False)

    batch_number = db.Column(db.String(50))
    quantity_received = db.Column(db.Integer, nullable=False)
    quantity_remaining = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(12, 2))
    selling_price = db.Column(db.Numeric(12, 2))
    expiry_date = db.Column(db.Date)

    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    received_at = db.Column(db.DateTime, default=now)

    drug = db.relationship("Drug")
    received_by = db.relationship("User")

    __table_args__ = (
        db.CheckConstraint("quantity_remaining >= 0", name="ck_stock_batch_qty_non_negative"),
        db.CheckConstraint("quantity_remaining <= quantity_received", name="ck_stock_batch_qty_not_over_received"),
    )


STOCK_TRANSACTION_TYPES = ["Receipt", "Dispense", "Return", "Adjustment", "Expiry", "Damage", "Transfer"]


class StockTransaction(db.Model):
    """Immutable ledger row for every movement against a StockBatch. This
    is what makes stock auditable: quantity_remaining on StockBatch is a
    derived running total, but this table is the source of truth for how
    it got there (who moved how much, when, why, and against which visit
    if it was a dispense)."""
    __tablename__ = "stock_transactions"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    stock_batch_id = db.Column(db.Integer, db.ForeignKey("stock_batches.id"), nullable=False)

    transaction_type = db.Column(db.String(20), nullable=False)
    # Positive for receipts/returns, negative for dispenses/expiries/damage/adjustments-down.
    quantity_delta = db.Column(db.Integer, nullable=False)
    quantity_after = db.Column(db.Integer, nullable=False)

    prescription_item_id = db.Column(db.Integer, db.ForeignKey("prescription_items.id"), nullable=True)
    reason = db.Column(db.String(255))

    performed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    stock_batch = db.relationship("StockBatch", backref="transactions")
    performed_by = db.relationship("User")

    __table_args__ = (
        db.CheckConstraint("quantity_delta != 0", name="ck_stock_txn_delta_nonzero"),
    )


# ---------------------------------------------------------------------------
# Billing & Insurance
# ---------------------------------------------------------------------------

BILL_STATUSES = ["Pending", "Partially Paid", "Paid", "Waived"]
PAYMENT_METHODS = ["Cash", "M-Pesa", "Card", "Insurance"]
BILL_LINE_CATEGORIES = ["Consultation", "Pharmacy", "Lab", "Radiology", "Procedure", "Admission", "Other"]


class Bill(db.Model):
    """One bill per visit. Line items are generated from whatever actually
    happened on that visit (consultation fee, dispensed drugs, resulted lab
    tests, reported radiology, bed days) so it reflects real charges, not
    estimates."""
    __tablename__ = "bills"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    insurance_scheme_id = db.Column(db.Integer, db.ForeignKey("insurance_schemes.id"), nullable=True)
    insurance_claim_number = db.Column(db.String(50))

    status = db.Column(db.String(20), default="Pending")
    notes = db.Column(db.String(300))

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    patient = db.relationship("Patient")
    insurance_scheme = db.relationship("InsuranceScheme")
    created_by = db.relationship("User")
    hospital = db.relationship("Hospital")
    line_items = db.relationship("BillLineItem", backref="bill", lazy=True)
    payments = db.relationship("Payment", backref="bill", lazy=True, order_by="Payment.paid_at.desc()")

    @property
    def total_amount(self):
        return round(float(sum(li.amount for li in self.line_items)), 2)

    @property
    def amount_paid(self):
        return round(float(sum(p.amount for p in self.payments)), 2)

    @property
    def balance_due(self):
        return round(self.total_amount - self.amount_paid, 2)

    def refresh_status(self):
        if self.status == "Waived":
            return
        if self.amount_paid <= 0:
            self.status = "Pending"
        elif self.amount_paid >= self.total_amount:
            self.status = "Paid"
        else:
            self.status = "Partially Paid"


class BillLineItem(db.Model):
    __tablename__ = "bill_line_items"

    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bills.id"), nullable=False)

    category = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), default=1)
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    source_type = db.Column(db.String(30))  # 'consultation', 'prescription_item', 'lab_order', 'radiology_order', 'admission'
    source_id = db.Column(db.Integer)

    __table_args__ = (
        db.CheckConstraint("amount >= 0", name="ck_bill_line_item_amount_non_negative"),
    )


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bills.id"), nullable=False)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    reference = db.Column(db.String(80))  # M-Pesa code, insurance claim ref, etc.

    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    paid_at = db.Column(db.DateTime, default=now)

    received_by = db.relationship("User")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        # Idempotency: the same provider reference (M-Pesa code, insurance
        # claim ref, etc.) can't be recorded twice for the same method.
        # Cash payments have no reference so they're excluded (Postgres only;
        # other dialects will still get uniqueness enforced at the app layer).
        db.Index(
            "uq_payment_method_reference",
            "method", "reference",
            unique=True,
            postgresql_where=db.text("reference IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# Medical documents — sick offs, medical certificates, referral letters,
# discharge summaries. One flexible model since they share most fields;
# document_type controls which fields the print layout actually shows.
# ---------------------------------------------------------------------------

DOCUMENT_TYPES = ["Sick Off", "Medical Certificate", "Referral Letter", "Discharge Summary"]
DOCUMENT_TYPE_PREFIXES = {
    "Sick Off": "SO",
    "Medical Certificate": "MC",
    "Referral Letter": "REF",
    "Discharge Summary": "DS",
}


class MedicalDocument(db.Model):
    __tablename__ = "medical_documents"
    __table_args__ = (db.UniqueConstraint("hospital_id", "document_number", name="uq_document_number_per_hospital"),)

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    document_type = db.Column(db.String(30), nullable=False)
    document_number = db.Column(db.String(40))

    diagnosis_code_id = db.Column(db.Integer, db.ForeignKey("diagnosis_codes.id"), nullable=True)
    body_text = db.Column(db.Text)              # main narrative: reason / clinical summary / hospital course
    recommendation = db.Column(db.Text)          # advice, discharge instructions, fitness note
    date_from = db.Column(db.Date)               # sick off start
    date_to = db.Column(db.Date)                 # sick off end
    days_count = db.Column(db.Integer)
    referred_to_facility = db.Column(db.String(200))
    referred_to_doctor = db.Column(db.String(150))
    follow_up_date = db.Column(db.Date)

    issued_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    issued_at = db.Column(db.DateTime, default=now)

    hospital = db.relationship("Hospital")
    patient = db.relationship("Patient")
    diagnosis_code = db.relationship("DiagnosisCode")
    issued_by = db.relationship("User")


# ---------------------------------------------------------------------------
# Platform subscription (billing the organization for using this system —
# distinct from Bill/Payment above, which is the organization's OWN patient
# billing). Price varies by the hospital level the organization operates
# at, since a Level 6 national referral hospital gets more value out of
# this system than a Level 2 dispensary.
# ---------------------------------------------------------------------------

# Monthly price in KES per plan level. Adjust freely — this is a starting
# point, not something the app enforces beyond "look this up when charging".
SUBSCRIPTION_PRICING = {
    "Level 1": 0,        # community units — not really billable software users
    "Level 2": 3000,
    "Level 3": 6000,
    "Level 4": 12000,
    "Level 5": 25000,
    "Level 6": 40000,
}

# One-time outright-purchase price in KES per plan level — a perpetual
# license with no recurring billing at all, for facilities that would
# rather own the system than lease it. Roughly priced around 18-24
# months of the equivalent monthly plan as a starting anchor; adjust
# freely, same as the monthly table above.
ONE_TIME_PRICING = {
    "Level 1": 0,
    "Level 2": 60000,
    "Level 3": 120000,
    "Level 4": 250000,
    "Level 5": 500000,
    "Level 6": 850000,
}

TRIAL_DAYS = 3
SUBSCRIPTION_PERIOD_DAYS = 30
PLAN_TYPES = ["subscription", "purchase"]


class SubscriptionPayment(db.Model):
    """One payment attempt against an organization's platform access,
    via IntaSend (M-Pesa STK push). status moves Pending -> Completed/
    Failed as the IntaSend webhook comes in. plan_type distinguishes a
    recurring monthly payment from a one-time outright purchase."""
    __tablename__ = "subscription_payments"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)

    plan_type = db.Column(db.String(20), default="subscription")  # 'subscription' or 'purchase'
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(10), default="KES")
    phone_number = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Pending")  # Pending, Completed, Failed
    invoice_id = db.Column(db.String(100))   # IntaSend's tracking_id / invoice reference
    api_ref = db.Column(db.String(100))       # our own reference, echoed back in the webhook
    provider = db.Column(db.String(30))       # M-PESA, CARD, etc.
    failure_reason = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    organization = db.relationship("Organization")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_subscription_payment_amount_positive"),
        # Prevents the same IntaSend webhook event being applied twice
        # (retries, duplicate delivery) from double-crediting an org.
        db.Index(
            "uq_subscription_payment_invoice_id",
            "invoice_id",
            unique=True,
            postgresql_where=db.text("invoice_id IS NOT NULL"),
        ),
    )


