"""End-to-end tests for the DHA compliance modules (need the real dependencies:
pip install -r requirements.txt pytest; run with `pytest tests/test_dha_integration.py`).
These were written against the code but could not be executed in the sandbox
that produced this release; run them before deploying."""
import datetime
import json

import pytest

from app import create_app
from app.extensions import db
from app.models import (Organization, Hospital, Role, Permission, User, Patient, Visit, DiagnosisCode, Consultation,
                        ConsultationDiagnosis, AuditLog, Drug)
from app.records.models import PatientAllergy, PatientProblem, PatientMedication
from app.reporting.models import NotifiableDisease, DiseaseNotification
from app.reporting import service as rsvc
from app.security.models import RecordVersion, EmergencyAccessGrant
from app.security import integrity, mfa_service, totp

PERMS = ("patient.view", "patient.register", "record.edit", "surveillance.manage", "emergency.access", "security.manage",
         "compliance.view", "audit.view", "prescription.create", "hie.manage")


class Cfg:
    TESTING = True
    SECRET_KEY = "t"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SUPPORT_WHATSAPP_NUMBER = "0"
    SESSION_COOKIE_SECURE = False
    HIE_MODE = "mock"


def _user(org, h, role, name):
    u = User(username=name, email=f"{name}@example.com", full_name=name, organization_id=org.id, hospital_id=h.id, role_id=role.id)
    u.set_password("pw12345678"); u.must_change_password = False
    db.session.add(u)
    return u


@pytest.fixture
def env():
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        rsvc.seed_notifiable_diseases(); rsvc.seed_quality_measures()
        org = Organization(name="O", plan_level="Level 4", subscription_status="active",
                           current_period_end=datetime.datetime.utcnow() + datetime.timedelta(days=30))
        db.session.add(org); db.session.flush()
        h1 = Hospital(organization_id=org.id, name="H1", code="H1", mfl_code="11111")
        h2 = Hospital(organization_id=org.id, name="H2", code="H2")
        db.session.add_all([h1, h2]); db.session.flush()
        role = Role(name="Clin", scope="department")
        role.permissions = [Permission(code=c, module="x") for c in PERMS]
        db.session.add(role); db.session.flush()
        doc, other = _user(org, h1, role, "doc"), _user(org, h2, role, "other")
        p = Patient(hospital_id=h1.id, patient_number="H1-1", first_name="A", last_name="B", gender="Female",
                    date_of_birth=datetime.date(1990, 1, 1), national_id="37722207", passport_number="A1234567")
        db.session.add(p); db.session.flush()
        v = Visit(hospital_id=h1.id, patient_id=p.id, visit_type="Outpatient", status="In Consultation")
        db.session.add(v); db.session.commit()
        c1, c2 = app.test_client(), app.test_client()
        for c, u in ((c1, doc), (c2, other)):
            with c.session_transaction() as s:
                s["_user_id"] = str(u.id); s["_fresh"] = True
        yield app, c1, c2, doc, other, p, v, h1


def test_identifiers_encrypted_in_db_but_readable_and_searchable(env):
    app, c1, _, doc, _, p, _, _ = env
    raw = db.session.execute(db.text("select national_id, passport_number from patients")).fetchone()
    assert raw[0].startswith("enc:v1:") and "37722207" not in raw[0] and raw[1].startswith("enc:v1:")
    assert db.session.get(Patient, p.id).national_id == "37722207"
    assert db.session.get(Patient, p.id).masked_national_id.endswith("2207")
    found = Patient.query.filter(Patient.national_id_idx == __import__("app.security.crypto", fromlist=["x"]).blind_index("3772-2207")).first()
    assert found and found.id == p.id


def test_audit_chain_seals_and_detects_edit(env):
    app, *_ = env
    from app.models import log_action
    log_action(None, "x", "T", 1); log_action(None, "y", "T", 2); db.session.commit()
    rows = AuditLog.query.order_by(AuditLog.id).all()
    assert all(r.entry_hash for r in rows) and rows[1].prev_hash == rows[0].entry_hash
    from app.compliance.service import _audit_check
    assert _audit_check()["ok"]
    db.session.execute(db.text("update audit_logs set details='tampered' where id=:i"), {"i": rows[0].id}); db.session.commit()
    assert rows[0].id in _audit_check()["tampered"]


def test_versioning_records_changes_and_amendment_reason(env):
    app, c1, _, doc, _, p, v, _ = env
    r = c1.post(f"/patients/{p.id}/problems", json={"description": "Malaria"})
    pid = r.get_json()["id"]
    assert c1.post(f"/problems/{pid}", json={"status": "Resolved"}).get_json()["success"]
    denied = c1.post(f"/problems/{pid}", json={"status": "Active"}).get_json()          # reopening a closed problem
    assert not denied["success"] and "reason" in denied["error"].lower()
    ok = c1.post(f"/problems/{pid}", json={"status": "Active", "amendment_reason": "Diagnosed in error"}).get_json()
    assert ok["success"]
    versions = RecordVersion.query.filter_by(entity="PatientProblem", entity_id=pid).order_by(RecordVersion.id).all()
    assert [x.action for x in versions] == ["create", "update", "update"] and versions[-1].is_amendment
    assert versions[-1].amendment_reason == "Diagnosed in error"
    with pytest.raises(Exception):
        versions[0].action = "x"; db.session.commit()


def test_national_id_never_in_version_history(env):
    app, c1, _, doc, _, p, _, _ = env
    pat = db.session.get(Patient, p.id); pat.national_id = "99999999"; db.session.commit()
    txt = " ".join((x.changes or "") + (x.snapshot or "") for x in RecordVersion.query.filter_by(entity="Patient").all())
    assert "99999999" not in txt and "37722207" not in txt


def test_emergency_access_read_only_time_limited_and_audited(env):
    app, c1, c2, doc, other, p, _, _ = env
    assert c2.get(f"/patients/{p.id}/record").status_code == 403                       # different hospital
    r = c2.post("/security/emergency-access", json={"patient_number": "H1-1", "reason": "Unconscious trauma patient, transferred"})
    assert r.get_json()["success"]
    assert c2.get(f"/patients/{p.id}/record").status_code == 200
    assert c2.post(f"/patients/{p.id}/allergies", json={"allergen_name": "X"}).status_code == 403   # never writable
    g = EmergencyAccessGrant.query.first(); g.expires_at = datetime.datetime.utcnow() - datetime.timedelta(minutes=1); db.session.commit()
    assert c2.get(f"/patients/{p.id}/record").status_code == 403
    assert AuditLog.query.filter_by(action="emergency_access_granted").count() == 1
    short = c2.post("/security/emergency-access", json={"patient_number": "H1-1", "reason": "urgent"})
    assert short.status_code == 400


def test_prescribing_blocked_by_allergy_until_override(env):
    app, c1, _, doc, _, p, v, h1 = env
    d = Drug(organization_id=doc.organization_id, name="Amoxicillin 500mg", generic_name="amoxicillin", price=1)
    db.session.add(d); db.session.commit()
    c1.post(f"/patients/{p.id}/allergies", json={"allergen_name": "Penicillin", "severity": "Severe"})
    body = {"items": [{"drug_id": d.id, "dosage": "500mg", "frequency": "TDS", "duration": "5 days", "quantity": 15}]}
    r = c1.post(f"/visits/{v.id}/prescriptions", json=body)
    assert r.status_code == 409 and r.get_json()["needs_override"]
    r = c1.post(f"/visits/{v.id}/prescriptions", json={**body, "override_reason": "Tolerated before, documented"})
    assert r.get_json()["success"]
    med = PatientMedication.query.filter_by(patient_id=p.id).first()
    assert med and med.status == "Active" and med.end_date == datetime.date.today() + datetime.timedelta(days=5)


def test_notifiable_disease_created_when_consultation_finalised(env):
    app, c1, _, doc, _, p, v, _ = env
    dx = DiagnosisCode(code="A00.9", description="Cholera, unspecified"); db.session.add(dx); db.session.flush()
    cons = Consultation(hospital_id=v.hospital_id, visit_id=v.id, patient_id=p.id, doctor_id=doc.id, diagnosis_code_id=dx.id)
    db.session.add(cons); db.session.commit()
    made = rsvc.detect_notifiable(cons, doc.id); db.session.commit()
    assert len(made) == 1 and made[0].disease.code == "CHOLERA" and made[0].status == "Pending"
    assert rsvc.detect_notifiable(cons, doc.id) == []                                  # no duplicates
    assert rsvc.submit_notification(made[0], {"SURVEILLANCE_MODE": "off"}) == "Not configured"
    assert rsvc.submit_notification(made[0], {"SURVEILLANCE_MODE": "mock"}) == "Submitted"


def test_hie_send_blocked_without_consent_then_mock_send(env):
    app, c1, _, doc, _, p, _, _ = env
    r = c1.post(f"/hie/patients/{p.id}/send").get_json()
    assert not r["success"] and r["status"] == "Blocked (no consent)"
    from app.consent import service as cs
    from app.consent.models import PatientConsent
    db.session.add(PatientConsent(patient_id=p.id, purpose="hie", granted=True, method="verbal", recorded_by_id=doc.id)); db.session.commit()
    r = c1.post(f"/hie/patients/{p.id}/send").get_json()
    assert r["success"] and r["status"] == "Mock"


def test_mfa_enrol_login_and_backup_code(env):
    app, c1, _, doc, _, p, _, _ = env
    secret, _ = mfa_service.begin_enrolment(doc)
    codes = mfa_service.confirm_enrolment(doc, totp.totp(secret))
    assert codes and mfa_service.is_enabled(doc)
    assert mfa_service.verify_login(doc, codes[0]) and not mfa_service.verify_login(doc, codes[0])   # single use
    fresh = app.test_client()
    r = fresh.post("/auth/login", json={"username": "doc", "password": "pw12345678"}).get_json()
    assert r.get("mfa_required") and fresh.get("/dashboard").status_code in (302, 401)
    assert fresh.post("/security/mfa/verify", json={"code": "000000"}).status_code == 401


def test_compliance_dashboard_renders_and_attestation_flow(env):
    app, c1, _, doc, _, p, _, _ = env
    assert c1.get("/compliance").status_code == 200
    assert c1.post("/compliance/attest", json={"item_key": "tls_enforced", "value": "1"}).get_json()["success"]
    assert c1.post("/compliance/attest", json={"item_key": "bogus", "value": "1"}).status_code == 400


def test_fhir_summary_download_valid(env):
    app, c1, _, *_ = env
    p = Patient.query.first()
    r = c1.get(f"/patients/{p.id}/summary.fhir.json")
    b = json.loads(r.data)
    from app.hie.fhir import validate_bundle
    assert validate_bundle(b) == [] and b["entry"][0]["resource"]["resourceType"] == "Composition"
