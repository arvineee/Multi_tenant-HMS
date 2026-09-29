import datetime
import hashlib
import pytest

from app import create_app
from app.extensions import db
from app.models import Organization, Hospital, Role, Permission, User, Patient
from app.consent import service
from app.consent.models import (PatientConsent, AuthorisedPerson, DigitalConsentEvidence,
                                CONSENT_STATEMENTS)

PNG = "data:image/png;base64,iVBORw0KGgo="


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
        for c in ("patient.view", "consent.manage", "audit.view"):
            role.permissions.append(Permission(code=c, module="x"))
        db.session.add(role); db.session.flush()
        u = User(username="clerk", email="c@example.com", full_name="C", organization_id=org.id,
                 hospital_id=h.id, role_id=role.id)
        u.set_password("pw12345678"); u.must_change_password = False
        db.session.add(u)
        p = Patient(hospital_id=h.id, patient_number="H1-1", first_name="Felex", last_name="Kipronoh",
                    national_id="37722207")
        db.session.add(p); db.session.flush()
        ap = AuthorisedPerson(patient_id=p.id, full_name="Jane Kipronoh", relationship="Mother",
                              scope="consent", recorded_by_id=u.id)
        db.session.add(ap); db.session.commit()
        client = app.test_client()
        with client.session_transaction() as s:
            s["_user_id"] = str(u.id); s["_fresh"] = True
        yield client, u, p, ap


def _post(client, p, **kw):
    body = {"purpose": "treatment", "granted": "true"}
    body.update(kw)
    return client.post(f"/patients/{p.id}/consent", json=body)


def test_method_must_be_chosen(ctx):
    client, _, p, _ = ctx
    r = _post(client, p)
    assert r.status_code == 400 and "how the consent was given" in r.get_json()["error"]
    assert PatientConsent.query.count() == 0


def test_written_and_verbal_still_work_without_evidence(ctx):
    client, _, p, _ = ctx
    assert _post(client, p, method="written").status_code == 200
    assert _post(client, p, method="verbal", purpose="hie").status_code == 200
    assert DigitalConsentEvidence.query.count() == 0


def test_digital_needs_tick_and_name(ctx):
    client, _, p, _ = ctx
    r = _post(client, p, method="digital", signer_name="Felex Kipronoh")            # no tick
    assert r.status_code == 400
    r = _post(client, p, method="digital", acknowledged="true")                      # no name
    assert r.status_code == 400
    r = _post(client, p, method="digital", acknowledged="true", signer_name="Someone Else")
    assert r.status_code == 400 and "must match Felex Kipronoh" in r.get_json()["error"]
    assert PatientConsent.query.count() == 0 and DigitalConsentEvidence.query.count() == 0


def test_digital_by_patient_stores_evidence(ctx):
    client, _, p, _ = ctx
    r = _post(client, p, method="digital", acknowledged="true",
              signer_name="  felex   KIPRONOH ", signature_png=PNG)
    assert r.status_code == 200
    assert service.has_consent(p.id, "treatment")
    ev = DigitalConsentEvidence.query.one()
    assert ev.signer_role == "patient" and ev.signature_png == PNG
    assert ev.statement_text == CONSENT_STATEMENTS["treatment"]
    assert ev.statement_sha256 == hashlib.sha256(ev.statement_text.encode()).hexdigest()
    assert ev.consent.method == "digital"


def test_digital_by_authorised_person_must_type_their_own_name(ctx):
    client, _, p, ap = ctx
    r = _post(client, p, method="digital", acknowledged="true",
              signer_name="Felex Kipronoh", authorised_person_id=ap.id)
    assert r.status_code == 400
    r = _post(client, p, method="digital", acknowledged="true",
              signer_name="Jane Kipronoh", authorised_person_id=ap.id)
    assert r.status_code == 200
    ev = DigitalConsentEvidence.query.one()
    assert ev.signer_role == "Mother (authorised person)"


def test_bad_signature_rejected(ctx):
    client, _, p, _ = ctx
    r = _post(client, p, method="digital", acknowledged="true",
              signer_name="Felex Kipronoh", signature_png="<script>alert(1)</script>")
    assert r.status_code == 400 and PatientConsent.query.count() == 0


def test_digital_withdrawal_records_withdrawal_wording(ctx):
    client, _, p, _ = ctx
    _post(client, p, method="written")
    r = _post(client, p, method="digital", granted="false", acknowledged="true",
              signer_name="Felex Kipronoh")
    assert r.status_code == 200 and not service.has_consent(p.id, "treatment")
    ev = DigitalConsentEvidence.query.one()
    assert ev.statement_text.startswith("I do NOT agree")


def test_patient_page_masks_national_id_and_requires_method_choice(ctx):
    client, _, p, _ = ctx
    html = client.get(f"/patients/{p.id}").get_data(as_text=True)
    assert "37722207" not in html and "****2207" in html
    assert "How was it given?" in html and 'id="digital-consent-box"' in html
