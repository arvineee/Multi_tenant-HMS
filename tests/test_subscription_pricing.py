"""System Maintainer-managed subscription pricing (per hospital / per level / all)."""
import datetime
import pytest

from app import create_app
from app.extensions import db
from app.models import (Organization, Hospital, Role, Permission, User, SubscriptionPayment,
                        SubscriptionPriceRule, AuditLog)
from app.subscription.pricing import resolve_price


class Cfg:
    TESTING = True
    SECRET_KEY = "t"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SUPPORT_WHATSAPP_NUMBER = "0"
    SESSION_COOKIE_SECURE = False
    MFA_REQUIRED_ROLES = []


def _org(name, level, code):
    org = Organization(name=name, plan_level=level, subscription_status="trial",
                       trial_ends_at=datetime.datetime.utcnow() - datetime.timedelta(days=1))
    db.session.add(org); db.session.flush()
    h = Hospital(organization_id=org.id, name=name, code=code, level=level)
    db.session.add(h); db.session.flush()
    role = Role.query.filter_by(name="CEO").first()
    u = User(username=f"ceo_{code}", email=f"{code}@example.com", full_name="C", organization_id=org.id,
             hospital_id=h.id, role_id=role.id)
    u.set_password("pw12345678"); u.must_change_password = False
    db.session.add(u); db.session.flush()
    return org, h, u


def _login(client, user):
    # the fixture keeps one app context open, so flask-login's cached
    # per-request user would leak from one simulated login to the next
    from flask import g
    g.pop("_login_user", None)
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id); s["_fresh"] = True


@pytest.fixture
def env():
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        ceo = Role(name="CEO", scope="organization")
        ceo.permissions.append(Permission(code="settings.edit", module="x"))
        maint = Role(name="System Maintainer", scope="platform")
        maint.permissions.append(Permission(code="system.maintain", module="x"))
        db.session.add_all([ceo, maint]); db.session.flush()

        mo = Organization(name="Maint", plan_level="Level 4", subscription_status="purchased")
        db.session.add(mo); db.session.flush()
        mh = Hospital(organization_id=mo.id, name="Maint HQ", code="M")
        db.session.add(mh); db.session.flush()
        m = User(username="maint", email="m@example.com", full_name="M", organization_id=mo.id,
                 hospital_id=mh.id, role_id=maint.id)
        m.set_password("pw12345678"); m.must_change_password = False
        db.session.add(m)

        gill, gill_h, gill_u = _org("Gillcare Medical Clinic", "Level 2", "GC")
        other2, other2_h, other2_u = _org("Other Dispensary", "Level 2", "O2")
        l3, l3_h, l3_u = _org("Level3 Hospital", "Level 3", "L3")
        db.session.commit()
        yield dict(app=app, client=app.test_client(), maint=m, gill=gill, gill_h=gill_h, gill_u=gill_u,
                   other2=other2, other2_u=other2_u, l3=l3, l3_u=l3_u)


def _add_rule(client, **body):
    return client.post("/system-maintenance/pricing", json=body)


def test_default_prices_when_no_rule(env):
    assert resolve_price(env["gill"], "subscription") == (3000, None)
    assert resolve_price(env["l3"], "purchase")[0] == 120000


def test_gillcare_1500_set_by_maintainer_only_affects_that_hospital(env):
    c = env["client"]; _login(c, env["maint"])
    r = _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500", note="Negotiated")
    assert r.status_code == 200 and r.get_json()["success"]
    amount, rule = resolve_price(env["gill"], "subscription")
    assert amount == 1500 and rule.note == "Negotiated"
    assert resolve_price(env["other2"], "subscription")[0] == 3000   # same level, different hospital
    assert resolve_price(env["gill"], "purchase")[0] == 60000        # purchase untouched


def test_level_rule_hits_every_hospital_at_that_level_only(env):
    c = env["client"]; _login(c, env["maint"])
    assert _add_rule(c, scope="all", level="Level 2", plan_type="subscription", amount="2000").status_code == 200
    assert resolve_price(env["gill"], "subscription")[0] == 2000
    assert resolve_price(env["other2"], "subscription")[0] == 2000
    assert resolve_price(env["l3"], "subscription")[0] == 6000


def test_all_hospitals_all_levels_and_precedence(env):
    c = env["client"]; _login(c, env["maint"])
    _add_rule(c, scope="all", level="", plan_type="subscription", amount="5000")
    _add_rule(c, scope="all", level="Level 2", plan_type="subscription", amount="2000")
    _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500")
    assert resolve_price(env["gill"], "subscription")[0] == 1500   # hospital beats level
    assert resolve_price(env["other2"], "subscription")[0] == 2000  # level beats all
    assert resolve_price(env["l3"], "subscription")[0] == 5000      # all-levels beats default


def test_inactive_rule_ignored_and_delete_restores_default(env):
    c = env["client"]; _login(c, env["maint"])
    rid = _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500").get_json()["id"]
    assert c.post(f"/system-maintenance/pricing/{rid}/toggle").get_json()["active"] is False
    assert resolve_price(env["gill"], "subscription")[0] == 3000
    c.post(f"/system-maintenance/pricing/{rid}/toggle")
    assert resolve_price(env["gill"], "subscription")[0] == 1500
    assert c.post(f"/system-maintenance/pricing/{rid}/update", json={"amount": "1800", "note": "x"}).get_json()["success"]
    assert resolve_price(env["gill"], "subscription")[0] == 1800
    c.post(f"/system-maintenance/pricing/{rid}/delete")
    assert resolve_price(env["gill"], "subscription")[0] == 3000


def test_validation_and_duplicates(env):
    c = env["client"]; _login(c, env["maint"])
    assert _add_rule(c, scope="hospital", hospital_id="", plan_type="subscription", amount="1").status_code == 400
    assert _add_rule(c, scope="all", plan_type="subscription", amount="-5").status_code == 400
    assert _add_rule(c, scope="all", plan_type="subscription", amount="abc").status_code == 400
    assert _add_rule(c, scope="all", plan_type="weekly", amount="5").status_code == 400
    assert _add_rule(c, scope="all", level="Level 9", plan_type="subscription", amount="5").status_code == 400
    assert _add_rule(c, scope="all", level="Level 2", plan_type="subscription", amount="5").status_code == 200
    assert _add_rule(c, scope="all", level="Level 2", plan_type="subscription", amount="6").status_code == 400  # duplicate


def test_only_system_maintainer_can_manage(env):
    gill = Organization.query.get(env["gill"].id)
    gill.subscription_status = "active"
    gill.current_period_end = datetime.datetime.utcnow() + datetime.timedelta(days=5)
    db.session.commit()
    c = env["client"]; _login(c, env["gill_u"])   # a facility CEO
    assert c.get("/system-maintenance/pricing").status_code == 403
    assert _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1").status_code == 403
    assert SubscriptionPriceRule.query.count() == 0
    # tampering with the checkout request can't set a price either
    r = c.post("/subscription/checkout", json={"phone_number": "0712345678", "plan_type": "subscription", "amount": 1})
    assert SubscriptionPayment.query.one().amount == 3000


def test_checkout_charges_resolved_price_and_page_shows_it(env):
    c = env["client"]; _login(c, env["maint"])
    _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500", note="Negotiated rate")
    c2 = env["app"].test_client(); _login(c2, env["gill_u"])
    page = c2.get("/subscription").get_data(as_text=True)
    assert "1,500" in page and "Negotiated rate" in page
    c2.post("/subscription/checkout", json={"phone_number": "0712345678", "plan_type": "subscription"})
    assert float(SubscriptionPayment.query.one().amount) == 1500
    # a different Level 2 facility is still charged the default
    c3 = env["app"].test_client(); _login(c3, env["other2_u"])
    c3.post("/subscription/checkout", json={"phone_number": "0712345678", "plan_type": "subscription"})
    assert sorted(float(p.amount) for p in SubscriptionPayment.query.all()) == [1500, 3000]


def test_zero_price_activates_without_payment_row(env):
    c = env["client"]; _login(c, env["maint"])
    _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="0")
    c2 = env["app"].test_client(); _login(c2, env["gill_u"])
    r = c2.post("/subscription/checkout", json={"phone_number": "", "plan_type": "subscription"})
    assert r.get_json()["success"] and r.get_json()["free"]
    org = Organization.query.get(env["gill"].id)
    assert org.has_access and SubscriptionPayment.query.count() == 0


def test_changes_are_audited(env):
    c = env["client"]; _login(c, env["maint"])
    _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500")
    assert AuditLog.query.filter_by(model_name="SubscriptionPriceRule", action="create").count() == 1


def test_pricing_page_renders_for_maintainer(env):
    c = env["client"]; _login(c, env["maint"])
    _add_rule(c, scope="hospital", hospital_id=env["gill_h"].id, plan_type="subscription", amount="1500")
    html = c.get("/system-maintenance/pricing").get_data(as_text=True)
    assert "Gillcare Medical Clinic" in html and "custom" in html
