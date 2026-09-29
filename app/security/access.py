"""Patient access checks shared by the new modules.

read access  : normal facility access, or an active emergency (break-glass) grant
write access : normal facility access only (a grant never allows changes)
Every refusal is audited; every read of a record section is audited.
"""
from flask import abort, jsonify
from flask_login import current_user

from app.extensions import db
from app.models import Patient, now
from app.audit.service import log_denied, log_view
from app.security.models import EmergencyAccessGrant


def active_grant(user, patient_id):
    return (EmergencyAccessGrant.query.filter(
        EmergencyAccessGrant.user_id == user.id, EmergencyAccessGrant.patient_id == patient_id,
        EmergencyAccessGrant.revoked_at.is_(None), EmergencyAccessGrant.expires_at > now()).first())


def load_patient(patient_id, write=False, section=None, action=None):
    patient = db.session.get(Patient, patient_id) or abort(404)
    ok = patient.hospital_id in current_user.accessible_hospital_ids()
    if not ok and not write and active_grant(current_user, patient.id):
        ok = True
    if not ok:
        log_denied(current_user, action or ("update" if write else "view"), patient.id, "outside accessible hospitals")
        abort(403)
    if section and not write:
        log_view(current_user, patient, section=section)
    return patient


def json_error(msg, code=400):
    return jsonify(success=False, error=msg), code


def ok(**kw):
    return jsonify(success=True, **kw)
