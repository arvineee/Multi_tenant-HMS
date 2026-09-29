import datetime
import pytest

from app import create_app
from app.extensions import db
from app.models import (Organization, Hospital, Role, Permission, User, Patient, Visit,
                        Bill, InsuranceScheme, AuditLog)
from app.consent import service
from app.consent.guards import consent_block_reason


class Cfg:
    TESTING = True
    SECRET_KEY = "t"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SUPPORT_WHATSAPP_NUMBER = "0"
    SESSION_COOKIE_SECURE = False


@pytest.fixture
def ctx():
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        org = Organization(name="O", plan_level="Level 4", subscription_status="active",
                           current_period_end=datetime.datetime.utcnow() + datetime.timedelta(days=30))
        db.session.add(org); db.session.flush()
        h = Hospital(organization_id=org.id, name="H", code="H1"); db.session.add(h); db.session.flush()
        role = Role(name="Clerk", scope="department")
        for c in ("patient.view", "billing.manage", "consent.manage", "audit.view"):
            role.permissions.append(Permission(code=c, module="x"))
        db.session.add(role); db.session.flush()
        u = User(username="clerk", email="c@example.com", full_name="C", organization_id=org.id,
                 hospital_id=h.id, role_id=role.id)
        u.set_password("pw12345678"); u.must_change_password = False
        db.session.add(u)
        sha = InsuranceScheme(organization_id=org.id, name="SHA", code="SHA", scheme_type="NHIF/SHA")
        private = InsuranceScheme(organization_id=org.id, name="AAR", code="AAR", scheme_type="Private")
        db.session.add_all([sha, private]); db.session.flush()
        p = Patient(hospital_id=h.id, patient_number="H1-1", first_name="A", last_name="B")
        db.session.add(p); db.session.flush()
        v1 = Visit(hospital_id=h.id, patient_id=p.id)
        v2 = Visit(hospital_id=h.id, patient_id=p.id)
        db.session.add_all([v1, v2]); db.session.flush()
        sha_bill = Bill(hospital_id=h.id, visit_id=v1.id, patient_id=p.id, insurance_scheme_id=sha.id)
        private_bill = Bill(hospital_id=h.id, visit_id=v2.id, patient_id=p.id, insurance_scheme_id=private.id)
        db.session.add_all([sha_bill, private_bill]); db.session.commit()
        client = app.test_client()
        with client.session_transaction() as s:
            s["_user_id"] = str(u.id); s["_fresh"] = True
        yield client, u, p, sha_bill, private_bill


def _claim(client, bill, number="CLM-1"):
    return client.post(f"/bills/{bill.id}/claim-number", json={"insurance_claim_number": number})


def test_sha_claim_blocked_without_consent_and_audited(ctx):
    client, u, p, sha_bill, _ = ctx
    r = _claim(client, sha_bill)
    assert r.status_code == 403 and r.get_json()["success"] is False
    assert Bill.query.get(sha_bill.id).insurance_claim_number is None
    assert AuditLog.query.filter_by(patient_id=p.id, action="sha_claim_submit", outcome="denied").count() == 1


def test_sha_claim_allowed_after_consent_and_blocked_again_after_withdrawal(ctx):
    client, u, p, sha_bill, _ = ctx
    service.record_consent(u, p, "sha_claims", True)
    assert _claim(client, sha_bill).status_code == 200
    assert Bill.query.get(sha_bill.id).insurance_claim_number == "CLM-1"
    service.record_consent(u, p, "sha_claims", False)
    assert _claim(client, sha_bill, "CLM-2").status_code == 403
    assert Bill.query.get(sha_bill.id).insurance_claim_number == "CLM-1"


def test_non_sha_scheme_is_not_blocked(ctx):
    client, _, _, _, private_bill = ctx
    assert _claim(client, private_bill).status_code == 200


def test_bill_page_warns_when_consent_missing(ctx):
    client, u, p, sha_bill, _ = ctx
    assert b"consent is missing" in client.get(f"/bills/{sha_bill.id}").data
    service.record_consent(u, p, "sha_claims", True)
    assert b"consent is missing" not in client.get(f"/bills/{sha_bill.id}").data


def test_hie_needs_its_own_consent(ctx):
    _, u, p, _, _ = ctx
    service.record_consent(u, p, "sha_claims", True)
    assert consent_block_reason(p.id, "hie") is not None
    service.record_consent(u, p, "hie", True)
    assert consent_block_reason(p.id, "hie") is None


def test_patient_page_shows_consent_panel_and_records_consent(ctx):
    client, u, p, _, _ = ctx
    page = client.get(f"/patients/{p.id}")
    assert page.status_code == 200 and b'id="consent-panel"' in page.data
    r = client.post(f"/patients/{p.id}/consent", json={"purpose": "sha_claims", "granted": "true", "method": "verbal"})
    assert r.status_code == 200 and service.has_consent(p.id, "sha_claims")
    assert b"Granted" in client.get(f"/patients/{p.id}").data
