"""Health Information Exchange outbox/inbox and e-prescription transmissions."""
from app.extensions import db
from app.models import now

HIE_STATUSES = ["Queued", "Sent", "Mock", "Failed", "Blocked (no consent)", "Received", "Applied", "Rejected"]


class HieTransaction(db.Model):
    """Outbound (or pulled) FHIR exchange. Every attempt is recorded."""
    __tablename__ = "hie_transactions"

    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(3), nullable=False, default="out")   # out / in
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), index=True)
    purpose = db.Column(db.String(30), default="hie")
    resource_summary = db.Column(db.String(200))              # e.g. "Bundle(document): 14 resources"
    bundle_json = db.Column(db.Text)                          # what was (or would be) sent
    status = db.Column(db.String(25), default="Queued", index=True)
    attempts = db.Column(db.Integer, default=0)
    http_status = db.Column(db.Integer)
    response_excerpt = db.Column(db.String(1000))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now)
    sent_at = db.Column(db.DateTime)

    patient = db.relationship("Patient")
    created_by = db.relationship("User")


class HieInbound(db.Model):
    """A FHIR message received from (or pulled from) the HIE. It is stored for
    a clinician to review; nothing is written into the chart automatically."""
    __tablename__ = "hie_inbound"

    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospitals.id"), index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), index=True)   # matched patient, if any
    received_at = db.Column(db.DateTime, default=now)
    source = db.Column(db.String(20), default="push")          # push / pull
    bundle_json = db.Column(db.Text, nullable=False)
    summary = db.Column(db.String(300))
    status = db.Column(db.String(15), default="Received")      # Received / Applied / Rejected
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)

    patient = db.relationship("Patient")


class PrescriptionTransmission(db.Model):
    """Electronic transmission of a prescription: to the in-house pharmacy
    worklist (always) and, when enabled and consented, to the HIE."""
    __tablename__ = "prescription_transmissions"

    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey("prescriptions.id"), nullable=False, index=True)
    channel = db.Column(db.String(20), nullable=False)         # pharmacy / hie
    status = db.Column(db.String(25), default="Sent")
    detail = db.Column(db.String(300))
    hie_transaction_id = db.Column(db.Integer, db.ForeignKey("hie_transactions.id"))
    created_at = db.Column(db.DateTime, default=now)

    prescription = db.relationship("Prescription", backref=db.backref("transmissions", lazy=True))
