"""Disease surveillance (immediate + weekly IDSR), public health events and
quality measures."""
import datetime

from app.extensions import db
from app.models import now

NOTIFICATION_STATUSES = ["Pending", "Submitted", "Failed", "Not configured", "Cancelled"]
EVENT_TYPES = ["Disease outbreak / cluster", "Unusual health event", "Rumour / community alert",
               "Environmental / chemical / radiological", "Other"]
EVENT_STATUSES = ["Detected", "Under investigation", "Confirmed", "Discarded", "Closed"]
ALERT_LEVELS = ["Low", "Medium", "High", "Critical"]


class NotifiableDisease(db.Model):
    """Reference list (shared by all organizations, like roles/permissions).
    category 'immediate' = report within 24h (MOH 502); 'weekly' = IDSR weekly
    (MOH 505). Seeded with defaults by reporting.service.seed_notifiable_diseases;
    confirm the list against the current MOH circular and edit it from
    Surveillance -> Disease list."""
    __tablename__ = "notifiable_diseases"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(10), nullable=False, default="immediate")
    icd10_prefixes = db.Column(db.String(200), nullable=False)   # comma separated, e.g. "A00"
    moh_form = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)

    @property
    def prefixes(self):
        return [p.strip().upper().replace(".", "") for p in (self.icd10_prefixes or "").split(",") if p.strip()]


class DiseaseNotification(db.Model):
    """One case notification (MOH 502-style). Created automatically when a
    consultation is finalised with a matching diagnosis."""
    __tablename__ = "disease_notifications"
    __versioned__ = True

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visits.id"))
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"))
    disease_id = db.Column(db.Integer, db.ForeignKey("notifiable_diseases.id"), nullable=False)
    diagnosis_code = db.Column(db.String(20))
    detected_at = db.Column(db.DateTime, default=now, nullable=False)
    date_of_onset = db.Column(db.Date)
    date_seen = db.Column(db.Date)
    lab_confirmed = db.Column(db.Boolean)
    outcome = db.Column(db.String(20))                       # Alive / Died / Unknown
    vaccination_status = db.Column(db.String(30))            # Vaccinated / Not vaccinated / Unknown
    notes = db.Column(db.String(500))
    status = db.Column(db.String(20), default="Pending", index=True)
    submitted_at = db.Column(db.DateTime)
    submission_reference = db.Column(db.String(100))
    last_error = db.Column(db.String(300))
    attempts = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    patient = db.relationship("Patient")
    disease = db.relationship("NotifiableDisease")
    hospital = db.relationship("Hospital")

    @property
    def hours_open(self):
        end = self.submitted_at or now()
        return round((end - self.detected_at).total_seconds() / 3600, 1)


class IdsrWeeklyReport(db.Model):
    """Aggregate weekly report for one facility and epidemiological week."""
    __tablename__ = "idsr_weekly_reports"
    __table_args__ = (db.UniqueConstraint("hospital_id", "epi_year", "epi_week", name="uq_idsr_week"),)

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    epi_year = db.Column(db.Integer, nullable=False)
    epi_week = db.Column(db.Integer, nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    week_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="Generated")   # Generated / Submitted / Failed / Not configured
    payload = db.Column(db.Text)                             # JSON {disease_code: {...counts}}
    total_outpatient_visits = db.Column(db.Integer, default=0)
    generated_at = db.Column(db.DateTime, default=now)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # NULL = automatic
    submitted_at = db.Column(db.DateTime)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    last_error = db.Column(db.String(300))

    hospital = db.relationship("Hospital")

    @property
    def label(self):
        return f"{self.epi_year}-W{self.epi_week:02d}"


class PublicHealthEvent(db.Model):
    __tablename__ = "public_health_events"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    date_detected = db.Column(db.Date, default=datetime.date.today)
    location = db.Column(db.String(200))
    cases = db.Column(db.Integer, default=0)
    deaths = db.Column(db.Integer, default=0)
    alert_level = db.Column(db.String(10), default="Medium")
    status = db.Column(db.String(25), default="Detected")
    source = db.Column(db.String(20), default="Manual")      # Manual / Auto-signal
    reported_to = db.Column(db.String(100))                  # e.g. "Sub-county disease surveillance coordinator"
    reported_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    hospital = db.relationship("Hospital")
    updates = db.relationship("PublicHealthEventUpdate", backref="event", lazy=True,
                              order_by="PublicHealthEventUpdate.created_at")


class PublicHealthEventUpdate(db.Model):
    __tablename__ = "public_health_event_updates"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("public_health_events.id"), nullable=False, index=True)
    note = db.Column(db.String(1000), nullable=False)
    status = db.Column(db.String(25))
    cases = db.Column(db.Integer)
    deaths = db.Column(db.Integer)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)

    created_by = db.relationship("User")


class QualityMeasure(db.Model):
    """Definition of a measure. Built-in ones are calculated from MediCore data
    (builtin_key); others can be imported and have values entered/imported."""
    __tablename__ = "quality_measures"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    numerator_desc = db.Column(db.String(300))
    denominator_desc = db.Column(db.String(300))
    higher_is_better = db.Column(db.Boolean, default=True)
    target_percent = db.Column(db.Float)
    builtin_key = db.Column(db.String(40))       # NULL for imported/manual measures
    is_active = db.Column(db.Boolean, default=True)


class QualityMeasureResult(db.Model):
    __tablename__ = "quality_measure_results"
    __table_args__ = (db.UniqueConstraint("measure_id", "hospital_id", "period_start", "period_end",
                                          name="uq_qm_result"),)

    id = db.Column(db.Integer, primary_key=True)
    measure_id = db.Column(db.Integer, db.ForeignKey("quality_measures.id"), nullable=False, index=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    numerator = db.Column(db.Integer, nullable=False, default=0)
    denominator = db.Column(db.Integer, nullable=False, default=0)
    source = db.Column(db.String(12), default="calculated")   # calculated / captured / imported
    notes = db.Column(db.String(300))
    status = db.Column(db.String(15), default="Draft")        # Draft / Submitted / Failed / Not configured
    calculated_at = db.Column(db.DateTime, default=now)
    calculated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    submitted_at = db.Column(db.DateTime)

    measure = db.relationship("QualityMeasure")
    hospital = db.relationship("Hospital")

    @property
    def rate(self):
        return round(100.0 * self.numerator / self.denominator, 1) if self.denominator else None


class SubmissionLog(db.Model):
    """Every electronic submission attempt (weekly reports, notifications,
    quality measures) — what was sent, where, and what came back."""
    __tablename__ = "submission_logs"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False)          # idsr_weekly / notification / quality_measure
    ref_id = db.Column(db.Integer)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"))
    mode = db.Column(db.String(10))                          # mock / live
    status = db.Column(db.String(15))                        # sent / failed / skipped
    http_status = db.Column(db.Integer)
    response_excerpt = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=now)
