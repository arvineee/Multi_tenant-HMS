"""Audit service — the one place the rest of MediCore records data *access*.

log_action() (app.models) already records writes inside the caller's
transaction. This adds the read side DHA-style rules ask for: who viewed
which patient's record, and who was refused.
"""
from app.extensions import db
from app.models import AuditLog, log_action


def log_view(user, patient, section="record", model_name="Patient"):
    """Record that `user` viewed `patient` (or a section of their record).

    Commits on its own, because a plain GET has no surrounding transaction
    and a view must be logged even if nothing else is saved.
    """
    log_action(user, "view", model_name, patient.id, {"section": section}, patient_id=patient.id)
    db.session.commit()


def log_denied(user, action, patient_id=None, reason=None, model_name="Patient"):
    """Record a refused attempt (403, missing consent, wrong hospital...)."""
    log_action(user, action, model_name, patient_id, {"reason": reason},
               patient_id=patient_id, outcome="denied")
    db.session.commit()


def access_history(patient_id, limit=200):
    """Everything logged against one patient, newest first — the answer to a
    data-subject's 'who has seen my record?'."""
    return (AuditLog.query.filter_by(patient_id=patient_id)
            .order_by(AuditLog.timestamp.desc()).limit(limit).all())
