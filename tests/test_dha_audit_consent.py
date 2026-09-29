import datetime
import pytest

from app import create_app
from app.extensions import db
from app.models import (Organization, Hospital, Role, Permission, User, Patient, AuditLog)
from app.consent import service
from app.consent.service import ConsentError
from app.audit.service import log_view, access_history


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
        role = Role(name="Doctor", scope="department")
        for c in ("patient.view", "consent.manage", "audit.view"):
            role.permissions.append(Permission(code=c, module="x"))
        db.session.add(role); db.session.flush()
        u = User(username="dr", email="dr@example.com", full_name="Dr", organization_id=org.id, hospital_id=h.id, role_id=role.id)
        u.set_password("pw12345678"); u.must_change_password = False
        db.session.add(u); db.session.flush()
        p = Patient(hospital_id=h.id, patient_number="H1-1", first_name="A", last_name="B")
        db.session.add(p); db.session.commit()
        yield app, u, p


def test_no_record_means_no_consent(ctx):
    _, u, p = ctx
    assert not service.has_consent(p.id, "sha_claims")


def test_grant_then_withdraw_keeps_history(ctx):
    _, u, p = ctx
    service.record_consent(u, p, "sha_claims", True)
    assert service.has_consent(p.id, "sha_claims")
    service.record_consent(u, p, "sha_claims", False)
    assert not service.has_consent(p.id, "sha_claims")
    from app.consent.models import PatientConsent
    assert PatientConsent.query.filter_by(patient_id=p.id).count() == 2


def test_authorised_person_rules(ctx):
    _, u, p = ctx
    ap = service.add_authorised_person(u, p, "Parent", "Mother", scope="information")
    with pytest.raises(ConsentError):
        service.record_consent(u, p, "hie", True, authorised_person_id=ap.id)  # info-only scope
    ap2 = service.add_authorised_person(u, p, "Guardian", "Aunt", scope="both",
                                        valid_until=datetime.date.today() - datetime.timedelta(days=1))
    with pytest.raises(ConsentError):
        service.record_consent(u, p, "hie", True, authorised_person_id=ap2.id)  # expired
    ap3 = service.add_authorised_person(u, p, "Guardian", "Aunt", scope="both")
    service.record_consent(u, p, "hie", True, authorised_person_id=ap3.id)
    assert service.has_consent(p.id, "hie")


def test_view_is_logged_and_history_queryable(ctx):
    _, u, p = ctx
    log_view(u, p)
    rows = access_history(p.id)
    assert len(rows) == 1 and rows[0].action == "view" and rows[0].patient_id == p.id


def test_audit_rows_are_append_only(ctx):
    _, u, p = ctx
    log_view(u, p)
    row = AuditLog.query.first()
    row.action = "tampered"
    with pytest.raises(RuntimeError):
        db.session.commit()
    db.session.rollback()
    db.session.delete(AuditLog.query.first())
    with pytest.raises(RuntimeError):
        db.session.commit()
    db.session.rollback()


def test_patient_page_logs_view_and_denies_other_hospital(ctx):
    app, u, p = ctx
    # second hospital's patient must be refused and the refusal logged
    h2 = Hospital(organization_id=u.organization_id, name="H2", code="H2"); db.session.add(h2); db.session.flush()
    p2 = Patient(hospital_id=h2.id, patient_number="H2-1", first_name="C", last_name="D")
    db.session.add(p2); db.session.commit()
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id); s["_fresh"] = True
    r = client.get(f"/patients/{p2.id}/consent")
    assert r.status_code == 403
    denied = AuditLog.query.filter_by(patient_id=p2.id, outcome="denied").count()
    assert denied == 1
    r = client.get(f"/patients/{p.id}/consent")
    assert r.status_code == 200
    assert AuditLog.query.filter_by(patient_id=p.id, action="view").count() == 1


# ---------------------------------------------------------------------------
# Hardening round: bulk-tamper guard, DB triggers, masking, route behaviour
# ---------------------------------------------------------------------------

def _client_for(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id); s["_fresh"] = True
    return client


def test_bulk_update_and_delete_of_audit_rows_are_refused(ctx):
    _, u, p = ctx
    log_view(u, p)
    with pytest.raises(RuntimeError):
        AuditLog.query.update({"action": "tampered"})
    db.session.rollback()
    with pytest.raises(RuntimeError):
        AuditLog.query.delete()
    db.session.rollback()
    assert AuditLog.query.count() == 1


def test_database_triggers_block_raw_sql_but_allow_inserts(ctx):
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError
    from add_dha_tables import install_audit_triggers
    _, u, p = ctx
    install_audit_triggers(db)
    install_audit_triggers(db)  # idempotent
    log_view(u, p)              # inserting still works
    with pytest.raises(DatabaseError):
        db.session.execute(text("UPDATE audit_logs SET action = 'x'"))
    db.session.rollback()
    with pytest.raises(DatabaseError):
        db.session.execute(text("DELETE FROM audit_logs"))
    db.session.rollback()
    assert AuditLog.query.count() == 1


def test_national_id_is_stored_masked(ctx):
    _, u, p = ctx
    ap = service.add_authorised_person(u, p, "Parent", "Mother", national_id="12345678")
    assert ap.national_id == "****5678"
    assert service.mask_national_id("") is None
    assert service.mask_national_id("123") == "***"


def test_missing_granted_is_rejected_not_treated_as_withdrawal(ctx):
    app, u, p = ctx
    client = _client_for(app, u)
    r = client.post(f"/patients/{p.id}/consent", json={"purpose": "hie"})
    assert r.status_code == 400
    from app.consent.models import PatientConsent
    assert PatientConsent.query.count() == 0
    r = client.post(f"/patients/{p.id}/consent", json={"purpose": "hie", "granted": True, "method": "written"})
    assert r.status_code == 200 and service.has_consent(p.id, "hie")
    r = client.post(f"/patients/{p.id}/consent", json={"purpose": "hie", "granted": False, "method": "written"})
    assert r.status_code == 200 and not service.has_consent(p.id, "hie")


def test_denied_attempts_record_what_was_attempted(ctx):
    app, u, p = ctx
    h2 = Hospital(organization_id=u.organization_id, name="H2", code="H2"); db.session.add(h2); db.session.flush()
    p2 = Patient(hospital_id=h2.id, patient_number="H2-1", first_name="C", last_name="D")
    db.session.add(p2); db.session.commit()
    client = _client_for(app, u)
    assert client.post(f"/patients/{p2.id}/consent", json={"purpose": "hie", "granted": True}).status_code == 403
    assert client.get(f"/patients/{p2.id}/access-history").status_code == 403
    actions = {r.action for r in AuditLog.query.filter_by(patient_id=p2.id, outcome="denied")}
    assert actions == {"consent_record", "access_history_view"}


def test_reading_access_history_is_itself_logged(ctx):
    app, u, p = ctx
    client = _client_for(app, u)
    assert client.get(f"/patients/{p.id}/access-history").status_code == 200
    assert AuditLog.query.filter_by(patient_id=p.id, action="view").count() == 1
    assert client.get(f"/patients/{p.id}/access-history?limit=1").status_code == 200


def test_revoke_authorised_person_route(ctx):
    app, u, p = ctx
    ap = service.add_authorised_person(u, p, "Guardian", "Aunt", scope="both")
    client = _client_for(app, u)
    assert client.post(f"/patients/{p.id}/authorised-persons/{ap.id}/revoke").status_code == 200
    with pytest.raises(ConsentError):
        service.record_consent(u, p, "hie", True, authorised_person_id=ap.id)
    assert AuditLog.query.filter_by(patient_id=p.id, action="authorised_person_revoked").count() == 1
    # someone else's / unknown person -> 404
    assert client.post(f"/patients/{p.id}/authorised-persons/9999/revoke").status_code == 404
