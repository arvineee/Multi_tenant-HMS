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


# ---------------------------------------------------------------------------
# DHA registry / eligibility / OTP lookups (app/hie/dha_client.py)
# ---------------------------------------------------------------------------
DHA_CONSENT_PURPOSE = "sha_claims"   # verifying a patient with DHA/SHA discloses their identifier, so it needs this consent
# identification_type spellings from DHA's docs. The Patient Search guide and the Eligibility Check guide
# spell them differently, so each endpoint has its own map. Passport is NOT a supported type in either.
SEARCH_ID_TYPES = {"National ID": "NATIONAL ID", "Birth Certificate": "BIRTH CERTIFICATE NUMBER", "Alien ID": "ALIEN ID",
                   "Refugee ID": "REFUGEE ID", "ClientRegistry ID": "CR ID"}
ELIGIBILITY_ID_TYPES = {"National ID": "National ID", "Birth Certificate": "Birth Certificate", "Alien ID": "Alien ID",
                        "Refugee ID": "Refugee ID", "ClientRegistry ID": "ClientRegistry ID"}


def id_type_map(purpose="eligibility"):
    raw = _cfg().get("HIE_SEARCH_ID_TYPE_MAP" if purpose == "search" else "HIE_ELIGIBILITY_ID_TYPE_MAP")
    if raw:
        try:
            m = json.loads(raw)
            if isinstance(m, dict):
                return m
        except ValueError:
            pass
    return SEARCH_ID_TYPES if purpose == "search" else ELIGIBILITY_ID_TYPES


def dha_client(hospital=None, transport=None):
    """One client per call, scoped to the patient's hospital via X-Facility-Id when it has a registry code."""
    from app.hie.dha_client import DhaHieClient
    c = DhaHieClient(_cfg(), transport)
    return c.for_facility(hospital.fr_code) if hospital is not None and getattr(hospital, "fr_code", None) else c


def patient_identifiers(patient):
    """[(label, value)] for the IDs DHA can look up. A Client Registry ID (once saved) comes first because
    DHA recommends it. Alien/Refugee ID numbers live in the main ID-number field, labelled by the ID type
    chosen at registration. Passport is excluded: DHA does not accept it for search or eligibility."""
    out = []
    if patient.dha_client_id:
        out.append(("ClientRegistry ID", patient.dha_client_id))
    if patient.national_id:
        out.append((patient.id_type if patient.id_type in ("Alien ID", "Refugee ID") else "National ID", patient.national_id))
    if patient.birth_certificate_number:
        out.append(("Birth Certificate", patient.birth_certificate_number))
    return out


def resolve_identifier(patient, purpose="eligibility", label=None):
    """Returns (dha_type, value, label) or raises ValueError with a message for the user."""
    ids = patient_identifiers(patient)
    if not ids:
        why = " DHA does not accept passports here." if patient.passport_number else ""
        raise ValueError("Record a national ID, birth certificate, alien/refugee ID or Client Registry ID first." + why)
    if label:
        ids = [x for x in ids if x[0] == label]
        if not ids:
            raise ValueError(f"No {label} on file for this patient.")
    lbl, value = ids[0]
    dha_type = id_type_map(purpose).get(lbl)
    if not dha_type:
        raise ValueError(f"No DHA identification type is configured for '{lbl}'.")
    return dha_type, value, lbl


def _dha_guard(user, patient):
    """Consent + configuration checks shared by every DHA call. Returns an error string or None."""
    reason = consent_block_reason(patient.id, DHA_CONSENT_PURPOSE)
    if reason:
        _log_action(user, "dha_lookup_blocked", "Patient", patient.id, {"reason": "no sha_claims consent"},
                    patient_id=patient.id, outcome="denied")
        db.session.commit()
        return reason
    return None


def dha_call(user, patient, kind, fn):
    """Runs `fn(client)` against DHA, audits it (never the identifier or response), returns (ok, data_or_error)."""
    from app.hie.models import DhaLookup
    err = _dha_guard(user, patient)
    if err:
        return False, err
    try:
        data = fn(dha_client(patient.hospital))
        status, detail, result = "ok", None, (True, data)
    except (HieError, ValueError) as e:
        status, detail, result = "failed", str(e)[:200], (False, str(e))
    except Exception as e:  # noqa: BLE001 — network/JSON problems must not become a 500 in front of a clerk
        status, detail, result = "failed", e.__class__.__name__, (False, "Could not reach DHA. Try again shortly.")
    db.session.add(DhaLookup(hospital_id=patient.hospital_id, patient_id=patient.id, user_id=user.id, kind=kind,
                             status=status, detail=detail))
    _log_action(user, f"dha_{kind}", "Patient", patient.id, {"status": status}, patient_id=patient.id,
                outcome="success" if status == "ok" else "denied")
    db.session.commit()
    return result


def extract_dha_patient_id(data):
    """Best-effort: DHA's search response shape is not verified, so we look in the usual places
    and always let the user confirm before saving anything."""
    def pick(d):
        if isinstance(d, dict):
            for k in ("patient_id", "crId", "cr_id", "memberCrNumber", "id", "client_id", "client_registry_id"):
                if d.get(k):
                    return str(d[k])
        return None
    if isinstance(data, dict):
        found = pick(data)
        if found:
            return found
        for k in ("data", "patient", "result", "results", "items"):
            inner = data.get(k)
            if isinstance(inner, list) and inner:
                inner = inner[0]
            found = pick(inner)
            if found:
                return found
    if isinstance(data, list) and data:
        return pick(data[0])
    return None
