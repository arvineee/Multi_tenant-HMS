"""Record versioning + amendment tracking (DHA audit trail: "tracks data
changes", "version tracking", "amendment tracking").

How it works: a session-level hook watches every flush. For any model marked
`__versioned__ = True` it writes a RecordVersion row in the SAME transaction
holding what changed (old -> new per field), the full state after the change,
who did it and when. Nothing in the routes has to remember to call it.

Amendments: once a clinical record is final (a completed consultation, a
resulted lab test...) further edits must carry a reason. Routes call
`require_amendment_reason(data, is_final)`; the reason is stored on the version
row and the row is flagged `is_amendment`.

Fields listed in `__version_redact__` (identifiers that are encrypted at rest)
are recorded as "[redacted]" so history never leaks what encryption protects.
"""
import datetime
import decimal
import json

from sqlalchemy import event, func, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.security.models import RecordVersion

_SKIP_FIELDS = {"updated_at"}
REDACTED = "[redacted]"


def mark_versioned(cls, redact=()):
    cls.__versioned__ = True
    cls.__version_redact__ = set(redact)
    return cls


def _is_versioned(obj):
    return bool(getattr(type(obj), "__versioned__", False))


def _json_safe(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "[binary]"
    return v


def _redact(obj):
    return getattr(type(obj), "__version_redact__", set())


def _snapshot(obj):
    redact = _redact(obj)
    out = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        k = attr.key
        if k in _SKIP_FIELDS:
            continue
        out[k] = REDACTED if k in redact else _json_safe(getattr(obj, k, None))
    return out


def _changes(obj):
    redact = _redact(obj)
    state = sa_inspect(obj)
    out = {}
    for attr in state.mapper.column_attrs:
        k = attr.key
        if k in _SKIP_FIELDS:
            continue
        hist = state.attrs[k].history
        if not hist.has_changes():
            continue
        old = hist.deleted[0] if hist.deleted else None
        new = hist.added[0] if hist.added else None
        if old == new:
            continue
        out[k] = [REDACTED, REDACTED] if k in redact else [_json_safe(old), _json_safe(new)]
    return out


def _actor():
    try:
        from flask import has_request_context
        if has_request_context():
            from flask_login import current_user
            if current_user and current_user.is_authenticated:
                return current_user.id
    except Exception:
        pass
    return None


def _amendment_reason():
    try:
        from flask import has_request_context, g
        if has_request_context():
            return getattr(g, "amendment_reason", None)
    except Exception:
        pass
    return None


@event.listens_for(Session, "before_flush")
def _collect(session, flush_context, instances):
    pending = session.info.setdefault("_rv_pending", [])
    for obj in list(session.new):
        if _is_versioned(obj):
            pending.append((obj, "create", None))
    for obj in list(session.dirty):
        if _is_versioned(obj) and session.is_modified(obj, include_collections=False):
            ch = _changes(obj)
            if ch:
                pending.append((obj, "update", ch))
    for obj in list(session.deleted):
        if _is_versioned(obj):
            pending.append((obj, "delete", _snapshot(obj)))


@event.listens_for(Session, "after_flush")
def _write(session, flush_context):
    pending = session.info.pop("_rv_pending", None)
    if not pending:
        return
    conn = session.connection()
    t = RecordVersion.__table__
    actor, reason = _actor(), _amendment_reason()
    now = datetime.datetime.utcnow()
    for obj, action, changes in pending:
        entity, entity_id = type(obj).__name__, getattr(obj, "id", None)
        if entity_id is None:
            continue
        count = conn.execute(select(func.count()).select_from(t).where(
            t.c.entity == entity, t.c.entity_id == entity_id)).scalar() or 0
        patient_id = getattr(obj, "patient_id", None)
        if entity == "Patient":
            patient_id = obj.id
        conn.execute(t.insert().values(
            entity=entity, entity_id=entity_id, patient_id=patient_id,
            hospital_id=getattr(obj, "hospital_id", None), version_no=count + 1, action=action,
            changes=json.dumps(changes, default=str) if changes is not None else None,
            snapshot=json.dumps(_snapshot(obj) if action != "delete" else changes, default=str),
            is_amendment=bool(reason) and action != "create", amendment_reason=reason if action != "create" else None,
            changed_by_id=actor, changed_at=now,
        ))


@event.listens_for(Session, "after_soft_rollback")
def _clear(session, previous_transaction):
    session.info.pop("_rv_pending", None)


@event.listens_for(RecordVersion, "before_update")
def _no_update(mapper, connection, target):
    raise RuntimeError("Record version history is append-only.")


@event.listens_for(RecordVersion, "before_delete")
def _no_delete(mapper, connection, target):
    raise RuntimeError("Record version history is append-only.")


@event.listens_for(Session, "do_orm_execute")
def _no_bulk(state):
    if (state.is_update or state.is_delete) and any(m.class_ is RecordVersion for m in state.all_mappers):
        raise RuntimeError("Record version history is append-only.")


# ---------------------------------------------------------------------------
# helpers used by routes
# ---------------------------------------------------------------------------

def require_amendment_reason(data, is_final, min_len=5):
    """Returns (reason, error). If the record is already final a reason is
    mandatory; when given, it is attached to every version row written during
    this request."""
    reason = (data.get("amendment_reason") or "").strip() if hasattr(data, "get") else ""
    if is_final and len(reason) < min_len:
        return None, "This record is already final. Enter a reason for the amendment."
    if is_final:
        from flask import g
        g.amendment_reason = reason[:300]
    return (reason or None), None


def history_for(entity, entity_id, limit=100):
    return (RecordVersion.query.filter_by(entity=entity, entity_id=entity_id)
            .order_by(RecordVersion.id.desc()).limit(limit).all())


def patient_history(patient_id, limit=200):
    return (RecordVersion.query.filter_by(patient_id=patient_id)
            .order_by(RecordVersion.id.desc()).limit(limit).all())


def register_defaults():
    """Mark the existing clinical models as versioned. Called once from create_app."""
    from app import models as m
    mark_versioned(m.Patient, redact=("national_id", "national_id_idx", "passport_number", "passport_number_idx",
                                      "birth_certificate_number", "birth_certificate_idx"))
    for cls in (m.Triage, m.Consultation, m.ConsultationDiagnosis, m.LabOrder, m.RadiologyOrder,
                m.Prescription, m.PrescriptionItem, m.MedicalDocument):
        mark_versioned(cls)
