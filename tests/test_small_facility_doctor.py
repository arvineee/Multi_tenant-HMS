"""At Level 1/2 a Doctor carries the whole visit (triage -> consult -> lab/X-ray -> dispense -> bill);
at Level 3+ the duties stay separate."""
import datetime
import pytest

from app import create_app
from app.extensions import db
from app.models import Organization, Hospital, Role, Permission, User, Patient, Visit


class Cfg:
    TESTING = True
    SECRET_KEY = "t"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SUPPORT_WHATSAPP_NUMBER = "0"
    SESSION_COOKIE_SECURE = False
    MFA_REQUIRED_ROLES = []


DOCTOR_PERMS = ("patient.view", "patient.register", "consultation.create", "prescription.create",
                "lab.order", "radiology.order")


def _make(org, level, code, role):
    h = Hospital(organization_id=org.id, name=f"H{code}", code=code, level=level)
    db.session.add(h); db.session.flush()
    u = User(username=f"doc{code}", email=f"{code}@example.com", full_name="D", organization_id=org.id,
             hospital_id=h.id, role_id=role.id)
    u.set_password("pw12345678"); u.must_change_password = False
    db.session.add(u); db.session.flush()
    p = Patient(hospital_id=h.id, patient_number=f"{code}-1", first_name="A", last_name="B")
    db.session.add(p); db.session.flush()
    v = Visit(hospital_id=h.id, patient_id=p.id)   # status defaults to Waiting
    db.session.add(v); db.session.flush()
    return h, u, v


@pytest.fixture
def env():
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        role = Role(name="Doctor", scope="department")
        for c in DOCTOR_PERMS:
            role.permissions.append(Permission(code=c, module="x"))
        db.session.add(role)
        org = Organization(name="O", plan_level="Level 2", subscription_status="active",
                           current_period_end=datetime.datetime.utcnow() + datetime.timedelta(days=30))
        db.session.add(org); db.session.flush()
        l1 = _make(org, "Level 1", "L1", role)
        l2 = _make(org, "Level 2", "L2", role)
        l3 = _make(org, "Level 3", "L3", role)
        l4 = _make(org, "Level 4", "L4", role)
        db.session.commit()
        yield app, dict(l1=l1, l2=l2, l3=l3, l4=l4)


def _client(app, user):
    from flask import g
    g.pop("_login_user", None)
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(user.id); s["_fresh"] = True
    return c


EXTRAS = ("triage.create", "lab.result", "radiology.report", "pharmacy.dispense", "billing.manage")


def test_level_1_and_2_doctor_gets_full_flow_permissions(env):
    _, e = env
    for key in ("l1", "l2"):
        u = e[key][1]
        for code in EXTRAS + DOCTOR_PERMS:
            assert u.has_permission(code), (key, code)


def test_level_3_and_4_doctor_unchanged(env):
    _, e = env
    for key in ("l3", "l4"):
        u = e[key][1]
        for code in EXTRAS:
            assert not u.has_permission(code), (key, code)
        assert u.has_permission("consultation.create")


def test_small_facility_doctor_still_lacks_admin_and_stock_permissions(env):
    _, e = env
    u = e["l2"][1]
    for code in ("pharmacy.stock", "pricing.manage", "users.manage", "catalogs.manage", "settings.edit"):
        assert not u.has_permission(code)


def test_role_itself_is_not_changed(env):
    _, e = env
    role = e["l2"][1].role
    assert not role.has_permission("triage.create") and not role.has_permission("billing.manage")


def test_level_2_doctor_can_triage_and_open_billing(env):
    app, e = env
    h, u, v = e["l2"]
    c = _client(app, u)
    r = c.post(f"/visits/{v.id}/triage", json={"priority": "Urgent", "temperature_c": "38.5"})
    assert r.status_code == 200 and r.get_json()["success"]
    assert Visit.query.get(v.id).status == "Triaged"
    assert c.get("/billing/worklist").status_code == 200
    h4, u4, _ = e["l4"]
    assert _client(app, u4).get("/billing/worklist").status_code == 403


def test_level_4_doctor_cannot_triage(env):
    app, e = env
    h, u, v = e["l4"]
    r = _client(app, u).post(f"/visits/{v.id}/triage", json={"priority": "Urgent"})
    assert r.status_code == 403


def test_queue_shows_waiting_patients_to_small_facility_doctor_only(env):
    app, e = env
    h, u, v = e["l2"]
    page = _client(app, u).get("/queue").get_data(as_text=True)
    assert "Patient Queue" in page and "Waiting" in page
    h4, u4, v4 = e["l4"]
    page4 = _client(app, u4).get("/queue").get_data(as_text=True)
    assert "Consultation Queue" in page4 and "Patient Queue" not in page4
