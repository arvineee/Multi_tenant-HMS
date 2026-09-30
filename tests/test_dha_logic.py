"""Pure-logic tests (growth, MCH, CDS, surveillance, FHIR, HIE client). No DB."""
import datetime
import importlib.util
import json
import pathlib
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, "/home/claude/shim")
import pytest  # noqa: E402

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _load(rel):
    spec = importlib.util.spec_from_file_location("_m_" + rel.replace("/", "_"), _APP / rel)
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    return m


growth, mch, rules = _load("records/growth.py"), _load("records/mch.py"), _load("cds/rules.py")
logic, fhir, client = _load("reporting/logic.py"), _load("hie/fhir.py"), _load("hie/client.py")


# ---- growth --------------------------------------------------------------
def test_lms_median_is_zero_and_known_value():
    assert growth.lms_zscore(9.6, 0.1, 9.6, 0.11) == 0.0
    z = growth.lms_zscore(12.0, -0.3, 10.0, 0.12)
    assert 1.4 < z < 1.8


def test_lms_L_zero_uses_log():
    assert growth.lms_zscore(11.0, 0, 10.0, 0.1) == round(__import__("math").log(1.1) / 0.1, 2)


def test_restricted_adjustment_beyond_3sd():
    raw = growth.lms_zscore(30, 0.1, 10, 0.11, adjust=False)
    adj = growth.lms_zscore(30, 0.1, 10, 0.11, adjust=True)
    assert raw > 3 and adj > 3 and adj != raw   # WHO linear extrapolation beyond +3 SD


def test_interpolation_and_range():
    rows = [(0, 1, 3.0, 0.1), (30, 1, 4.0, 0.1)]
    assert growth.interpolate_row(rows, 15) == (1, 3.5, 0.1)
    assert growth.interpolate_row(rows, 31) is None and growth.interpolate_row([], 1) is None


def test_classify_and_muac():
    assert growth.classify("wfa", -3.4) == "Severely underweight"
    assert growth.classify("wfl", -2.5) == "Moderate acute malnutrition"
    assert growth.muac_class(11.0, 12) == "Severe acute malnutrition"
    assert growth.muac_class(11.0, 3) is None


# ---- MCH -----------------------------------------------------------------
def test_mch_validation():
    ok = mch.validate("ANC", {"anc_visit_number": "2", "hb_g_dl": "10.5", "hiv_status": "Negative", "syphilis_tested": "on"})
    assert ok["anc_visit_number"] == 2 and ok["hb_g_dl"] == 10.5 and ok["syphilis_tested"] is True
    for bad in ({}, {"anc_visit_number": "x"}, {"anc_visit_number": 1, "hiv_status": "Maybe"}, {"anc_visit_number": -1}):
        with pytest.raises(mch.MchValidationError):
            mch.validate("ANC", bad)
    with pytest.raises(mch.MchValidationError):
        mch.validate("Nope", {})


def test_pregnancy_inference():
    t = datetime.date(2026, 9, 28)
    anc = ("ANC", t - datetime.timedelta(days=30), {})
    assert mch.is_pregnant_from([anc], t)
    assert not mch.is_pregnant_from([anc, ("Delivery", t - datetime.timedelta(days=5), {})], t)
    assert not mch.is_pregnant_from([("ANC", t - datetime.timedelta(days=400), {})], t)
    assert not mch.is_pregnant_from([], t)


def test_due_vaccines():
    dob = datetime.date(2026, 6, 1)
    due = mch.due_vaccines(dob, ["BCG"], datetime.date(2026, 9, 28))  # ~17 weeks
    assert "OPV 1" in due and "BCG" not in due and "Measles-Rubella 1" not in due


# ---- CDS -----------------------------------------------------------------
def _ctx(**kw):
    return rules.Context(**kw)


def test_allergy_exact_and_cross():
    amox = rules.DrugInfo(1, "Amoxicillin 500mg", "amoxicillin")
    ctx = _ctx(allergies=[{"allergen_name": "Amoxicillin", "status": "Active", "severity": "Severe"}])
    a = rules.check_allergy(amox, ctx)
    assert a and a[0].rule_id == "allergy_conflict" and a[0].severity == "critical" and a[0].requires_override
    ctx2 = _ctx(allergies=[{"allergen_name": "Penicillin", "status": "Active"}])
    assert rules.check_allergy(amox, ctx2)[0].rule_id == "allergy_cross_reaction"
    ctx3 = _ctx(allergies=[{"allergen_name": "Penicillin", "status": "Resolved"}])
    assert rules.check_allergy(amox, ctx3) == []
    assert rules.check_allergy(rules.DrugInfo(2, "Paracetamol", "paracetamol"), ctx2) == []


def test_allergy_by_drug_id():
    d = rules.DrugInfo(7, "Brand X", "")
    assert rules.check_allergy(d, _ctx(allergies=[{"allergen_name": "whatever", "drug_id": 7, "status": "Active"}]))


def test_duplicate_pregnancy_age():
    d = rules.DrugInfo(3, "Ibuprofen", "ibuprofen", pregnancy_caution=True, min_age_months=3)
    ctx = _ctx(age_months=2, pregnant=True, active_medications=[{"drug_id": 3, "name": "Ibuprofen"}])
    ids = {a.rule_id for a in rules.check_prescription(d, ctx)}
    assert {"duplicate_therapy", "pregnancy_caution", "pediatric_age"} <= ids
    assert [a.rule_id for a in rules.check_duplicate(d, _ctx(), [3, 3])] == ["duplicate_therapy"]


def test_labs_and_vitals():
    assert rules.check_lab_result("K+", "6.9", 3.5, 5.0, 2.5, 6.5, "mmol/L").rule_id == "critical_lab"
    assert rules.check_lab_result("K+", "5.4", 3.5, 5.0, 2.5, 6.5).rule_id == "abnormal_lab"
    assert rules.check_lab_result("K+", "4.0", 3.5, 5.0, 2.5, 6.5) is None
    assert rules.check_lab_result("x", "Positive") is None
    v = rules.check_vitals(_ctx(age_months=400, vitals={"spo2_percent": 85, "bp_systolic": 190, "bp_diastolic": 100}))
    assert {a.severity for a in v} == {"critical"} and len(v) == 2
    assert rules.check_vitals(_ctx(age_months=24, vitals={"bp_systolic": 200})) == []  # BP rule is adults only


def test_problem_context_and_switches():
    ctx = _ctx(problems=[{"description": "Essential hypertension", "status": "Active"}], vitals={"bp_systolic": 150, "bp_diastolic": 95})
    assert rules.check_problem_context(ctx)
    out = rules.evaluate_all(ctx, enabled=lambda r: r != "chronic_review")
    assert all(a.rule_id != "chronic_review" for a in out)
    d = rules.DrugInfo(1, "Amoxicillin", "amoxicillin")
    c2 = _ctx(allergies=[{"allergen_name": "penicillin", "status": "Active"}])
    assert rules.evaluate_all(c2, [d], enabled=lambda r: r != "allergy_conflict") == []
    res = rules.evaluate_all(_ctx(vitals={"spo2_percent": 80}, age_months=500, allergies=[{"allergen_name": "Amoxicillin", "status": "Active"}]), [d])
    assert res[0].severity == "critical"


# ---- surveillance --------------------------------------------------------
def _dis(code, prefixes, active=True):
    return NS(code=code, prefixes=[p.replace(".", "") for p in prefixes.split(",")], is_active=active)


def test_disease_matching_longest_prefix():
    ds = [_dis("MALARIA", "B50,B51"), _dis("VHF", "A98.4,A96"), _dis("X", "A9"), _dis("OFF", "A00", False)]
    assert logic.match_disease("B50.9", ds).code == "MALARIA"
    assert logic.match_disease("a98.4", ds).code == "VHF"
    assert logic.match_disease("A99", ds).code == "X"
    assert logic.match_disease("A00.1", ds) is None and logic.match_disease("Z00", ds) is None


def test_epi_week_and_previous():
    y, w, s, e = logic.epi_week(datetime.date(2026, 9, 28))     # Monday
    assert (y, w) == (2026, 40) and s == datetime.date(2026, 9, 28) and e == datetime.date(2026, 10, 4)
    assert logic.epi_week(datetime.date(2027, 1, 1))[:2] == (2026, 53)
    assert logic.previous_epi_week(datetime.date(2026, 9, 29))[:2] == (2026, 39)


def test_weekly_aggregate_and_exports():
    p = logic.aggregate_weekly([
        {"disease_code": "MALARIA", "age_years": 3, "outcome": "Alive"},
        {"disease_code": "MALARIA", "age_years": 30, "outcome": "Died"},
        {"disease_code": "MALARIA", "age_years": None}])
    assert p["MALARIA"]["<5y"] == {"cases": 1, "deaths": 0} and p["MALARIA"]["5y+"]["deaths"] == 1
    assert p["MALARIA"]["unknown"]["cases"] == 1
    xml = logic.build_sdmx_generic("IDSR", "KE.DHA", "DSD_IDSR", logic.weekly_to_series(p, "12345", "2026-W39"))
    assert xml.startswith(b"<?xml") and b"MALARIA_CASES" in xml and b'value="12345"' in xml
    body = logic.dhis2_data_value_set(p, "OU1", "2026W39")
    assert body["period"] == "2026W39" and any(v["dataElement"] == "MALARIA_DEATHS_5y+" and v["value"] == "1" for v in body["dataValues"])


def test_default_disease_list_sane():
    codes = [d[0] for d in logic.DEFAULT_NOTIFIABLE]
    assert len(codes) == len(set(codes)) and {d[2] for d in logic.DEFAULT_NOTIFIABLE} == {"immediate", "weekly"}


# ---- FHIR + client ---------------------------------------------------------
def _summary():
    return {
        "generated_at": "2026-09-28T10:00:00", "facility": {"id": 1, "name": "Demo", "mfl_code": "12345", "level": "Level 4", "county": "Nairobi"},
        "patient": {"id": 9, "patient_number": "N-1", "first_name": "Jane", "last_name": "Doe", "gender": "Female", "dob": "1990-01-01",
                    "identifiers": [{"type": "National ID", "value": "37722207"}], "phone": "0700", "address": "Rd", "county": "Nairobi",
                    "next_of_kin": {"name": "Sam", "relationship": "Brother", "phone": "0711"}},
        "problems": [{"id": 1, "description": "Malaria", "code": "B54", "status": "Active", "onset": "2026-09-20"}],
        "problems_checked": True,
        "allergies": [{"id": 1, "name": "Penicillin", "type": "Drug", "reaction": "Rash", "severity": "Severe", "status": "Active"}],
        "allergy_status": "Has allergies",
        "medications": [{"id": 1, "name": "Artemether/lumefantrine", "dosage": "80/480", "frequency": "BD", "status": "Active", "start": "2026-09-27"}],
        "prescriptions": [{"id": 5, "date": "2026-09-27", "items": [{"name": "Paracetamol", "dosage": "1g", "frequency": "TDS"}]}],
        "vitals": [{"id": 3, "date": "2026-09-27T09:00:00", "temperature_c": 38.5, "bp_systolic": 120, "bp_diastolic": 80, "weight_kg": 60}],
        "labs": [{"id": 4, "name": "Malaria RDT", "loinc": "70569-9", "value": "Positive", "date": "2026-09-27"},
                 {"id": 5, "name": "Hb", "loinc": "718-7", "value": "10.5 g/dL", "unit": "g/dL", "date": "2026-09-27"}],
        "care_plans": [{"problem": "Fever", "goal": "Afebrile", "interventions": "Tepid sponge", "status": "Active"}],
        "encounters": [{"id": 2, "type": "Outpatient", "date": "2026-09-27", "reason": "Fever"}],
        "immunizations": [],
    }


def test_bundle_structure_valid_and_deterministic():
    b = fhir.build_document_bundle(_summary(), "https://x.example/id")
    assert b["type"] == "document" and b["entry"][0]["resource"]["resourceType"] == "Composition"
    assert fhir.validate_bundle(b) == []
    types = {e["resource"]["resourceType"] for e in b["entry"]}
    assert {"Patient", "Organization", "Condition", "AllergyIntolerance", "MedicationRequest", "Observation", "CarePlan", "Encounter"} <= types
    b2 = fhir.build_document_bundle(_summary(), "https://x.example/id")
    assert [e["fullUrl"] for e in b["entry"]] == [e["fullUrl"] for e in b2["entry"]]
    bp = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation" and "component" in e["resource"]]
    assert bp and bp[0]["code"]["coding"][0]["code"] == "85354-9"
    assert "Bundle(document)" in fhir.summarize_bundle(b)


def test_bundle_no_empty_fields_and_empty_allergy_semantics():
    s = _summary(); s["allergies"] = []; s["allergy_status"] = "No known allergies"; s["patient"]["email"] = ""
    b = fhir.build_document_bundle(s, "u")
    pat = b["entry"][1]["resource"]
    assert not any(t.get("value") is None for t in pat["telecom"]) and all(t["system"] != "email" for t in pat["telecom"])
    comp = b["entry"][0]["resource"]
    al = [x for x in comp["section"] if x["title"].startswith("Allergies")][0]
    assert al["emptyReason"]["coding"][0]["code"] == "nilknown"
    s["allergy_status"] = "Unknown"
    al = [x for x in fhir.build_document_bundle(s, "u")["entry"][0]["resource"]["section"] if x["title"].startswith("Allergies")][0]
    assert al["emptyReason"]["coding"][0]["code"] == "unavailable"


def test_validate_catches_problems():
    assert fhir.validate_bundle({"resourceType": "Bundle", "type": "document", "entry": []})
    bad = {"resourceType": "Bundle", "type": "document", "entry": [
        {"fullUrl": "urn:uuid:a", "resource": {"resourceType": "Composition", "subject": {"reference": "urn:uuid:missing"}}}]}
    assert any("missing" in e for e in fhir.validate_bundle(bad))


def test_extract_identifiers():
    b = {"entry": [{"resource": {"resourceType": "Patient", "identifier": [{"value": "123", "type": {"text": "National ID"}}]}}]}
    assert fhir.extract_identifiers(b)[0]["value"] == "123"


class _Resp:
    def __init__(self, code, text="ok", js=None): self.status_code, self.text, self._js = code, text, js
    def json(self): return self._js


class _T:
    def __init__(self, resp): self.resp, self.calls = resp, []
    def post(self, url, **kw): self.calls.append((url, kw)); return self.resp
    def get(self, url, **kw): self.calls.append((url, kw)); return self.resp


def test_client_modes():
    with pytest.raises(client.HieError):
        client.HieClient({"HIE_MODE": "off"}).send_bundle({})
    st, http, _ = client.HieClient({"HIE_MODE": "mock"}).send_bundle({})
    assert st == "Mock" and http is None
    t = _T(_Resp(201, "created"))
    c = client.HieClient({"HIE_MODE": "live", "HIE_BASE_URL": "https://hie.example/fhir/", "HIE_API_TOKEN": "tok"}, transport=t)
    assert c.send_bundle({"a": 1})[:2] == ("Sent", 201)
    assert t.calls[0][0] == "https://hie.example/fhir/Bundle" and t.calls[0][1]["headers"]["Authorization"] == "Bearer tok"
    assert client.HieClient({"HIE_MODE": "live", "HIE_BASE_URL": "https://h"}, transport=_T(_Resp(500, "boom"))).send_bundle({})[0] == "Failed"


def test_client_oauth_caches_token():
    t = _T(_Resp(200, js={"access_token": "abc", "expires_in": 3600}))
    c = client.HieClient({"HIE_MODE": "live", "HIE_BASE_URL": "https://h", "HIE_AUTH_TYPE": "oauth2", "HIE_TOKEN_URL": "https://t"}, transport=t)
    assert c._headers()["Authorization"] == "Bearer abc"
    c._headers()
    assert len(t.calls) == 1


# ---- DHA registry / eligibility / OTP client ---------------------------------
def _dha():
    import types
    pkg = sys.modules.setdefault("app", types.ModuleType("app")); hie = types.ModuleType("app.hie")
    sys.modules["app.hie"] = hie; sys.modules["app.hie.client"] = client
    return _load("hie/dha_client.py")


CFG = {"HIE_MODE": "live", "HIE_BASE_URL": "https://uat.example/", "HIE_CLIENT_ID": "cid", "HIE_CLIENT_SECRET": "sec"}


class _Seq:
    """Fake transport: token endpoint, then scripted API responses."""
    def __init__(self, *api): self.api, self.calls = list(api), []
    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if url.endswith("/tenants/token"):
            return _Resp(200, js={"access_token": f"tok{sum(1 for c in self.calls if c[1].endswith('/token'))}", "expires_in": 3600})
        return self.api.pop(0)
    def get(self, url, **kw):
        self.calls.append(("get", url, kw)); return self.api.pop(0)


def test_dha_requires_live_mode_and_credentials():
    d = _dha()
    with pytest.raises(client.HieError):
        d.DhaHieClient({"HIE_MODE": "mock"}).search_patient("1", "national_id")
    with pytest.raises(client.HieError):
        d.DhaHieClient({"HIE_MODE": "live", "HIE_BASE_URL": "https://x"}).token()


def test_dha_token_body_caching_and_bearer():
    d = _dha(); t = _Seq(_Resp(200, js={"ok": 1}), _Resp(200, js={"ok": 2}))
    c = d.DhaHieClient(CFG, transport=t)
    c.search_patient("37722207", "national_id"); c.eligibility("37722207", "national_id")
    tok_calls = [x for x in t.calls if x[1].endswith("/api/v1/tenants/token")]
    assert len(tok_calls) == 1 and tok_calls[0][2]["json"] == {"client_id": "cid", "client_secret": "sec"}
    assert t.calls[1][1] == "https://uat.example/api/v1/patients"
    assert t.calls[1][2]["headers"]["Authorization"] == "Bearer tok1"
    assert t.calls[1][2]["params"] == {"identification_number": "37722207", "identification_type": "national_id"}
    assert t.calls[2][1].endswith("/api/v1/patients/eligibility")


def test_dha_endpoints_and_params():
    d = _dha(); t = _Seq(*[_Resp(200, js={}) for _ in range(8)]); c = d.DhaHieClient(CFG, transport=t)
    c.benefits("P1"); c.sub_benefits("P1", "SHA-19"); c.interventions("P1", "SHA-19-SC-13"); c.otp_contacts("P1")
    c.send_otp("P1", ["I1", "I2"]); c.send_otp("P1", ["I1"], "C2")
    api = [x for x in t.calls if "/tenants/token" not in x[1]]
    assert api[0][1].endswith("/api/v1/patients/benefits") and api[0][2]["params"] == {"patient_id": "P1"}
    assert api[1][1].endswith("/patients/sub-benefits") and api[1][2]["params"] == {"patient_id": "P1", "parent_benefit_code": "SHA-19"}
    assert api[2][1].endswith("/patients/benefits/interventions") and api[2][2]["params"] == {"patient_id": "P1", "sub_benefit_code": "SHA-19-SC-13"}
    assert api[3][1].endswith("/api/v1/patients/contacts")
    assert api[4][0] == "post" and api[4][1].endswith("/claims/otp") and api[4][2]["json"] == {"patient_id": "P1", "intervention_codes": ["I1", "I2"]}
    assert api[5][2]["json"] == {"patient_id": "P1", "intervention_codes": ["I1"], "contact_id": "C2"}
    with pytest.raises(client.HieError):
        c.send_otp("P1", [])                                   # DHA requires intervention_codes


def test_dha_401_refreshes_token_once_then_errors_without_leaking_body():
    d = _dha(); t = _Seq(_Resp(401, "expired"), _Resp(200, js={"ok": True})); c = d.DhaHieClient(CFG, transport=t)
    assert c.search_patient("1", "national_id") == {"ok": True}
    assert sum(1 for x in t.calls if x[1].endswith("/token")) == 2
    t2 = _Seq(_Resp(500, "X" * 5000)); c2 = d.DhaHieClient(CFG, transport=t2)
    with pytest.raises(client.HieError) as e:
        c2.eligibility("1", "national_id")
    assert len(str(e.value)) < 300 and "XXXXXXXXXX" in str(e.value)[:300]
    t3 = _Seq(_Resp(401, "no"), _Resp(401, "no")); c3 = d.DhaHieClient(CFG, transport=t3)
    with pytest.raises(client.HieError):
        c3.search_patient("1", "national_id")            # a second 401 is not retried forever


def test_dha_token_failures():
    d = _dha()
    class T:
        def post(self, *a, **k): return _Resp(403)
    with pytest.raises(client.HieError):
        d.DhaHieClient(CFG, transport=T()).token()
    class T2:
        def post(self, *a, **k): return _Resp(200, js={"nope": 1})
    with pytest.raises(client.HieError):
        d.DhaHieClient(CFG, transport=T2()).token()
    class T3:
        calls = []
        def post(self, url, **k): T3.calls.append(url); return _Resp(200, js={"access_token": "a"})
    d.DhaHieClient({**CFG, "HIE_TOKEN_URL": "https://auth.example/t"}, transport=T3()).token()
    assert T3.calls == ["https://auth.example/t"]


def test_dha_facility_headers_sent_together_and_token_shared():
    d = _dha(); t = _Seq(_Resp(200, js={}), _Resp(200, js={}), _Resp(200, js={}))
    base = d.DhaHieClient(CFG, transport=t)
    base.for_facility("FID-47-115307-8").search_patient("1", "NATIONAL ID")
    base.for_facility("FID-01-000001-1").eligibility("1", "National ID")
    base.search_patient("1", "NATIONAL ID")                       # no facility: token-claim scoping, no headers
    api = [x for x in t.calls if "/tenants/token" not in x[1]]
    assert api[0][2]["headers"]["X-Facility-Id"] == "FID-47-115307-8" and api[0][2]["headers"]["X-Facility-Id-Type"] == "fr-code"
    assert api[1][2]["headers"]["X-Facility-Id"] == "FID-01-000001-1"
    assert "X-Facility-Id" not in api[2][2]["headers"] and "X-Facility-Id-Type" not in api[2][2]["headers"]
    assert sum(1 for x in t.calls if x[1].endswith("/tenants/token")) == 1     # one token shared by all facilities


def test_dha_eligibility_flags_pomsf_prefix_and_otp_rules():
    d = _dha()
    data = {"data": {"fullName": "A B", "memberCrNumber": "CR123-4", "isAlive": True, "whitelistedForOTP": True,
                     "facilityBiometricsEnforced": False, "schemes": [{"schemeName": "SHIF"}, {"schemeName": "POMSF-047"}]}}
    f = d.eligibility_flags(data)
    assert f["cr_id"] == "CR123-4" and f["schemes"] == ["SHIF", "POMSF-047"] and f["whitelisted_for_otp"] is True
    assert d.is_pomsf(f["schemes"]) and not d.is_pomsf(["SHIF"]) and d.is_pomsf(["USALAMA"])   # POMSF matched by PREFIX
    assert d.otp_allowed(f) == (True, None)
    assert not d.otp_allowed({**f, "biometrics_enforced": True})[0]
    assert not d.otp_allowed({**f, "whitelisted_for_otp": False})[0]
    assert "deceased" in d.otp_allowed({**f, "is_alive": False})[1]
    assert d.otp_allowed({"is_alive": None, "whitelisted_for_otp": None, "biometrics_enforced": None})[0]  # unknown flags don't block
    assert d.eligibility_flags({"isAlive": False})["is_alive"] is False and d.eligibility_flags(None)["schemes"] == []
