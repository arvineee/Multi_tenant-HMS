"""Kenya HIE exchange: consent-gated outbound clinical summaries, inbound
message handling, e-prescription transmission."""
import datetime
import hmac
import hashlib
import json

from flask import current_app

from app.extensions import db
from app.models import Patient, Prescription, log_action
from app.audit.service import log_denied
from app.models import log_action as _log_action
from app.consent import service as consent_service
from app.consent.guards import consent_block_reason
from app.hie import fhir
from app.hie.client import HieClient, HieError
from app.hie.models import HieTransaction, HieInbound, PrescriptionTransmission
from app.security.crypto import blind_index
from app.summary.service import gather_summary


def _cfg():
    return current_app.config


def client(transport=None):
    return HieClient(_cfg(), transport)


def send_patient_summary(user, patient, transport=None):
    """Builds the clinical-summary bundle and sends it, but ONLY with 'hie' consent.
    Every outcome (including a consent refusal) is stored and audited."""
    cfg = _cfg()
    tx = HieTransaction(direction="out", hospital_id=patient.hospital_id, patient_id=patient.id, purpose="hie",
                        created_by_id=user.id if user else None)
    db.session.add(tx)
    reason = consent_block_reason(patient.id, "hie")
    if reason:
        tx.status, tx.response_excerpt = "Blocked (no consent)", reason
        log_denied(user, "share_hie", patient.id, "no 'hie' consent")
        db.session.commit()
        return tx
    bundle = fhir.build_document_bundle(gather_summary(patient), cfg["HIE_IDENTIFIER_BASE"])
    errs = fhir.validate_bundle(bundle)
    tx.bundle_json, tx.resource_summary = json.dumps(bundle), fhir.summarize_bundle(bundle)
    if errs:
        tx.status, tx.response_excerpt = "Failed", "Validation: " + "; ".join(errs)[:900]
    else:
        try:
            tx.status, tx.http_status, tx.response_excerpt = client(transport).send_bundle(bundle)
            tx.attempts = 1
            if tx.status in ("Sent", "Mock"):
                tx.sent_at = datetime.datetime.utcnow()
        except HieError as e:
            tx.status, tx.response_excerpt = "Failed", str(e)
    log_action(user, "hie_send", "HieTransaction", None, {"status": tx.status, "patient": patient.id},
               patient_id=patient.id, outcome="success" if tx.status in ("Sent", "Mock") else "denied")
    db.session.commit()
    return tx


def retry(user, tx, transport=None):
    if tx.status not in ("Failed",) or not tx.bundle_json:
        raise ValueError("Only failed transmissions can be retried.")
    if consent_block_reason(tx.patient_id, "hie"):
        tx.status = "Blocked (no consent)"
        db.session.commit()
        return tx
    try:
        tx.status, tx.http_status, tx.response_excerpt = client(transport).send_bundle(json.loads(tx.bundle_json))
    except HieError as e:
        tx.status, tx.response_excerpt = "Failed", str(e)
    tx.attempts = (tx.attempts or 0) + 1
    if tx.status in ("Sent", "Mock"):
        tx.sent_at = datetime.datetime.utcnow()
    log_action(user, "hie_retry", "HieTransaction", tx.id, {"status": tx.status}, patient_id=tx.patient_id)
    db.session.commit()
    return tx


def verify_signature(secret, body, header_value):
    """HMAC-SHA256 hex of the raw request body, sent as X-Signature (optionally 'sha256=' prefixed)."""
    if not secret or not header_value:
        return False
    given = header_value.split("=", 1)[-1].strip()
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, given)


def match_patient(bundle, hospital_ids):
    """Find a local patient from inbound identifiers using blind indexes (never plain-text search)."""
    for ident in fhir.extract_identifiers(bundle):
        idx = blind_index(ident["value"])
        if not idx:
            continue
        q = Patient.query.filter(Patient.hospital_id.in_(hospital_ids)).filter(
            db.or_(Patient.national_id_idx == idx, Patient.passport_number_idx == idx,
                   Patient.birth_certificate_idx == idx, Patient.dha_client_id == ident["value"]))
        p = q.first()
        if p:
            return p
    return None


def store_inbound(bundle, hospital_id, hospital_ids, source="push"):
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("Expected a FHIR Bundle.")
    p = match_patient(bundle, hospital_ids)
    row = HieInbound(hospital_id=p.hospital_id if p else hospital_id, patient_id=p.id if p else None, source=source,
                     bundle_json=json.dumps(bundle), summary=fhir.summarize_bundle(bundle))
    db.session.add(row)
    db.session.flush()
    log_action(None, "hie_receive", "HieInbound", row.id, {"matched": bool(p)}, patient_id=p.id if p else None)
    return row


def prescription_bundle(prescription):
    """E-prescription: MedicationRequests plus the diagnoses, diagnostic tests, problem list and medication lists
    that travel with it."""
    from app.records.models import PatientProblem, PatientMedication
    v = prescription.visit
    s = gather_summary(prescription.patient)
    s["prescriptions"] = [rx for rx in s["prescriptions"] if rx["id"] == prescription.id]
    s["medications"] = [m for m in s["medications"] if m["status"] == "Active"]
    tests = [{"id": o.id, "name": o.lab_test.name, "loinc": o.lab_test.loinc_code, "value": o.result_value or "Ordered",
              "unit": o.lab_test.unit, "date": o.ordered_at.date().isoformat()} for o in v.lab_orders]
    s["labs"] = tests
    return fhir.build_document_bundle(s, _cfg()["HIE_IDENTIFIER_BASE"])


def transmit_prescription(user, prescription, transport=None):
    """Always: to the in-house pharmacy worklist. Also to the HIE when enabled and consented."""
    rows = [PrescriptionTransmission(prescription_id=prescription.id, channel="pharmacy", status="Sent",
                                     detail="Available on the pharmacy worklist")]
    if _cfg().get("HIE_MODE") in ("mock", "live"):
        if consent_block_reason(prescription.patient_id, "hie"):
            rows.append(PrescriptionTransmission(prescription_id=prescription.id, channel="hie", status="Blocked (no consent)",
                                                 detail="Patient has not consented to HIE sharing"))
            _log_action(user, "share_hie", "Patient", prescription.patient_id, {"reason": "no 'hie' consent (e-prescription)"},
                        patient_id=prescription.patient_id, outcome="denied")  # no commit: part of the prescribing transaction
        else:
            tx = HieTransaction(direction="out", hospital_id=prescription.hospital_id, patient_id=prescription.patient_id,
                                purpose="hie", created_by_id=user.id if user else None)
            bundle = prescription_bundle(prescription)
            tx.bundle_json, tx.resource_summary = json.dumps(bundle), "E-prescription " + fhir.summarize_bundle(bundle)
            try:
                tx.status, tx.http_status, tx.response_excerpt = client(transport).send_bundle(bundle)
            except HieError as e:
                tx.status, tx.response_excerpt = "Failed", str(e)
            db.session.add(tx)
            db.session.flush()
            rows.append(PrescriptionTransmission(prescription_id=prescription.id, channel="hie", status=tx.status,
                                                 detail=(tx.response_excerpt or "")[:300], hie_transaction_id=tx.id))
    db.session.add_all(rows)
    return rows
