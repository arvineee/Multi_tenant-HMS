"""Consent service. Other modules (sha_claims, hie) must call
has_consent() before sending a patient's data out — never query the
consent tables directly."""
import hashlib
import re

from app.extensions import db
from app.models import Patient, log_action
from app.consent.models import (PatientConsent, AuthorisedPerson, DigitalConsentEvidence,
                                CONSENT_PURPOSES, CONSENT_METHODS, CONSENT_STATEMENTS,
                                WITHDRAWAL_STATEMENT)

MAX_SIGNATURE_CHARS = 300_000   # a drawn PNG data URL is normally well under 50 KB


class ConsentError(ValueError):
    pass


def mask_national_id(value):
    """Keep only the last 4 characters (e.g. '****5678'). We need enough to
    recognise the person at the desk, not a full ID number sitting in the DB."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def current_consent(patient_id, purpose):
    """Latest decision for this purpose, or None if never recorded."""
    return (PatientConsent.query.filter_by(patient_id=patient_id, purpose=purpose)
            .order_by(PatientConsent.recorded_at.desc(), PatientConsent.id.desc()).first())


def has_consent(patient_id, purpose):
    """True only if the most recent decision is a grant. No record = no consent."""
    row = current_consent(patient_id, purpose)
    return bool(row and row.granted)


def consent_summary(patient_id):
    return {p: current_consent(patient_id, p) for p in CONSENT_PURPOSES}


def _norm_name(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def statement_for(purpose, granted=True):
    """The exact text shown on screen for a digital confirmation."""
    base = CONSENT_STATEMENTS[purpose]
    if granted:
        return base
    return WITHDRAWAL_STATEMENT + CONSENT_PURPOSES[purpose].lower() + "."


def _build_digital_evidence(patient, purpose, granted, ap, digital):
    """Validate what the person did on screen. Raises ConsentError if it is
    incomplete, so a staff member cannot mark a consent 'digital' on their own."""
    digital = digital or {}
    if not digital.get("acknowledged"):
        raise ConsentError("The person must tick the confirmation box themselves.")
    signer = re.sub(r"\s+", " ", (digital.get("signer_name") or "").strip())
    if not signer:
        raise ConsentError("The person confirming must type their full name.")
    expected = ap.full_name if ap else patient.full_name
    if _norm_name(signer) != _norm_name(expected):
        raise ConsentError(f"The typed name must match {expected}.")
    sig = digital.get("signature_png") or None
    if sig:
        if not sig.startswith("data:image/png;base64,") or len(sig) > MAX_SIGNATURE_CHARS:
            raise ConsentError("The signature could not be read. Clear it and sign again.")
    text = statement_for(purpose, granted)
    return DigitalConsentEvidence(
        signer_name=signer,
        signer_role=f"{ap.relationship} (authorised person)" if ap else "patient",
        statement_text=text,
        statement_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        signature_png=sig,
        ip_address=(digital.get("ip_address") or "")[:64] or None,
        user_agent=(digital.get("user_agent") or "")[:255] or None,
    )


def record_consent(user, patient, purpose, granted, method="written",
                   authorised_person_id=None, notes=None, digital=None):
    """method='digital' additionally needs `digital` (see _build_digital_evidence):
    the person's own on-screen confirmation. The web form makes staff pick the
    method explicitly; the 'written' default is only for internal callers."""
    if purpose not in CONSENT_PURPOSES:
        raise ConsentError("Unknown consent purpose.")
    if method not in CONSENT_METHODS:
        raise ConsentError("Choose how the consent was given (written, verbal or digital).")
    ap = None
    if authorised_person_id:
        ap = AuthorisedPerson.query.filter_by(id=authorised_person_id, patient_id=patient.id).first()
        if not ap or not ap.is_valid or ap.scope not in ("consent", "both"):
            raise ConsentError("That person is not currently authorised to give consent for this patient.")
    evidence = None
    if method == "digital":
        evidence = _build_digital_evidence(patient, purpose, bool(granted), ap, digital)
    row = PatientConsent(
        patient_id=patient.id, hospital_id=patient.hospital_id, purpose=purpose,
        granted=bool(granted), method=method, notes=(notes or "")[:255] or None,
        given_by_authorised_person_id=authorised_person_id, recorded_by_id=user.id,
    )
    db.session.add(row)
    db.session.flush()
    if evidence:
        evidence.consent_id = row.id
        db.session.add(evidence)
    log_action(user, "consent_granted" if granted else "consent_withdrawn", "PatientConsent",
               row.id, {"purpose": purpose, "method": method}, patient_id=patient.id)
    db.session.commit()
    return row


def add_authorised_person(user, patient, full_name, relationship, national_id=None,
                          phone=None, scope="consent", valid_until=None):
    if not (full_name or "").strip() or not (relationship or "").strip():
        raise ConsentError("Name and relationship are required.")
    if scope not in ("consent", "information", "both"):
        raise ConsentError("Invalid scope.")
    ap = AuthorisedPerson(
        patient_id=patient.id, full_name=full_name.strip(), relationship=relationship.strip(),
        national_id=mask_national_id(national_id), phone=phone or None, scope=scope,
        valid_until=valid_until, recorded_by_id=user.id,
    )
    db.session.add(ap)
    db.session.flush()
    log_action(user, "authorised_person_added", "AuthorisedPerson", ap.id,
               {"relationship": ap.relationship, "scope": scope}, patient_id=patient.id)
    db.session.commit()
    return ap


def revoke_authorised_person(user, ap):
    ap.is_active = False
    log_action(user, "authorised_person_revoked", "AuthorisedPerson", ap.id,
               patient_id=ap.patient_id)
    db.session.commit()
