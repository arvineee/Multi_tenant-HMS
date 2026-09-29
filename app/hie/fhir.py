"""FHIR R4 builders for the Kenya HIE (duck-typed, no database access).

Input is the plain-dict summary produced by app.summary.service.gather_summary,
so everything here is unit-testable. Identifier system URIs default to a
placeholder base (HIE_IDENTIFIER_BASE): replace with the URIs DHA publishes
for the national ID, client registry, facility (MFL) and health-worker
registries before certification. The structure is standard FHIR R4; the
profile-specific constraints of Kenya's implementation guide still need to be
validated against DHA's validator.
"""
import datetime
import uuid

_NS = uuid.UUID("6f1d4c3a-2b7e-4a51-9d3e-0c9f5b1a7e42")
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
ICD11 = "http://id.who.int/icd/release/11/mms"
V3_ACT = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

VITAL_CODES = {
    "temperature_c": ("8310-5", "Body temperature", "Cel"),
    "pulse_bpm": ("8867-4", "Heart rate", "/min"),
    "respiratory_rate": ("9279-1", "Respiratory rate", "/min"),
    "spo2_percent": ("2708-6", "Oxygen saturation in Arterial blood", "%"),
    "weight_kg": ("29463-7", "Body weight", "kg"),
    "height_cm": ("8302-2", "Body height", "cm"),
    "bmi": ("39156-5", "Body mass index (BMI)", "kg/m2"),
}
BP_PANEL = ("85354-9", "Blood pressure panel")
BP_SYS = ("8480-6", "Systolic blood pressure")
BP_DIA = ("8462-4", "Diastolic blood pressure")


def rid(kind, key):
    """Deterministic resource id so re-sending the same record updates, not duplicates."""
    return str(uuid.uuid5(_NS, f"{kind}:{key}"))


def ref(kind, key):
    return {"reference": f"urn:uuid:{rid(kind, key)}"}


def _id_system(base, name):
    return f"{base.rstrip('/')}/{name}"


def _clean(d):
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if v not in (None, "", [], {})}
    if isinstance(d, list):
        return [x for x in (_clean(i) for i in d) if x not in (None, "", [], {})]
    return d


def _coding(system, code, display=None):
    return {"system": system, "code": code, "display": display} if code else None


def _entry(kind, key, resource):
    resource = _clean(resource)
    resource["id"] = rid(kind, key)
    return {"fullUrl": f"urn:uuid:{rid(kind, key)}", "resource": resource}


def patient_resource(s, base):
    p, fac = s["patient"], s["facility"]
    idents = []
    for i in p.get("identifiers", []):
        idents.append({"use": "official", "type": {"text": i["type"]},
                       "system": _id_system(base, i["type"].lower().replace(" ", "-")), "value": i["value"]})
    idents.append({"use": "usual", "type": {"text": "Facility patient number"},
                   "system": _id_system(base, f"facility/{fac.get('mfl_code') or fac.get('id')}/patient-number"),
                   "value": p["patient_number"]})
    if p.get("dha_client_id"):
        idents.append({"use": "official", "type": {"text": "Client Registry ID"},
                       "system": _id_system(base, "client-registry"), "value": p["dha_client_id"]})
    addr = {"text": p.get("address"), "district": p.get("sub_county"), "state": p.get("county"),
            "city": p.get("village") or p.get("ward"), "country": "KE"}
    kin = p.get("next_of_kin") or {}
    return {
        "resourceType": "Patient", "identifier": idents,
        "name": [{"use": "official", "family": p["last_name"], "given": [p["first_name"]]}],
        "gender": {"Male": "male", "Female": "female"}.get(p.get("gender"), "unknown"),
        "birthDate": p.get("dob"),
        "extension": [{"url": f"{base}/estimated-age", "valueInteger": p["estimated_age_years"]}] if p.get("estimated_age_years") is not None and not p.get("dob") else None,
        "telecom": [{"system": k, "value": p[k]} for k in ("phone", "email") if p.get(k)],
        "address": [addr],
        "contact": [{"relationship": [{"text": kin.get("relationship")}], "name": {"text": kin.get("name")},
                     "telecom": [{"system": "phone", "value": kin["phone"]}] if kin.get("phone") else None}] if kin.get("name") else None,
        "managingOrganization": ref("Organization", fac["id"]),
    }


def organization_resource(s, base):
    f = s["facility"]
    return {"resourceType": "Organization", "active": True, "name": f["name"],
            "identifier": [{"system": _id_system(base, "mfl"), "value": f.get("mfl_code")}] if f.get("mfl_code") else None,
            "type": [{"text": f.get("level")}], "address": [{"state": f.get("county"), "district": f.get("sub_county"), "country": "KE"}]}


def condition_resource(c, s):
    codings = [_coding(ICD10, c.get("code")), _coding(ICD11, c.get("icd11")), _coding(SNOMED, c.get("snomed"))]
    status = c.get("status", "Active")
    clin = {"Active": "active", "Resolved": "resolved", "Inactive": "inactive", "Ruled Out": "inactive"}.get(status, "active")
    r = {"resourceType": "Condition", "subject": ref("Patient", s["patient"]["id"]),
         "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": clin}]},
         "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "problem-list-item"}]}],
         "code": {"coding": [x for x in codings if x], "text": c["description"]},
         "onsetDateTime": c.get("onset"), "abatementDateTime": c.get("resolved")}
    if status == "Ruled Out":
        r["verificationStatus"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "refuted"}]}
    return r


def allergy_resource(a, s):
    crit = {"Mild": "low", "Moderate": "low", "Severe": "high", "Life-threatening": "high"}.get(a.get("severity"))
    sev = {"Mild": "mild", "Moderate": "moderate", "Severe": "severe", "Life-threatening": "severe"}.get(a.get("severity"))
    r = {"resourceType": "AllergyIntolerance", "patient": ref("Patient", s["patient"]["id"]),
         "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                        "code": "active" if a.get("status") == "Active" else "inactive"}]},
         "category": [{"Drug": "medication", "Food": "food", "Environmental": "environment"}.get(a.get("type"), "biologic")],
         "criticality": crit,
         "code": {"coding": [_coding(SNOMED, a.get("snomed")),
                             _coding(_id_system(s["_base"], "hpt"), a.get("hpt"))], "text": a["name"]},
         "reaction": [{"manifestation": [{"text": a["reaction"]}], "severity": sev}] if a.get("reaction") else None}
    r["code"]["coding"] = [c for c in r["code"]["coding"] if c]
    return r


def med_request_resource(m, s):
    return {"resourceType": "MedicationRequest",
            "status": {"Active": "active", "Completed": "completed", "Stopped": "stopped", "On hold": "on-hold"}.get(m.get("status"), "active"),
            "intent": "order", "subject": ref("Patient", s["patient"]["id"]),
            "medicationCodeableConcept": {"coding": [_coding(_id_system(s["_base"], "hpt"), m.get("hpt_code"))], "text": m["name"]},
            "authoredOn": m.get("start"),
            "dosageInstruction": [{"text": " ".join(x for x in [m.get("dosage"), m.get("frequency"), m.get("duration"),
                                                                 m.get("instructions")] if x)}],
            "reasonCode": [{"text": m["indication"]}] if m.get("indication") else None}


def vitals_observations(v, s):
    out = []
    date = v.get("date")
    for key, (code, disp, unit) in VITAL_CODES.items():
        if v.get(key) is None:
            continue
        out.append(("vital", f"{v['id']}:{key}", {
            "resourceType": "Observation", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
            "code": {"coding": [_coding(LOINC, code, disp)]}, "subject": ref("Patient", s["patient"]["id"]),
            "effectiveDateTime": date,
            "valueQuantity": {"value": v[key], "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}}))
    if v.get("bp_systolic") is not None and v.get("bp_diastolic") is not None:
        out.append(("vital", f"{v['id']}:bp", {
            "resourceType": "Observation", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
            "code": {"coding": [_coding(LOINC, *BP_PANEL)]}, "subject": ref("Patient", s["patient"]["id"]), "effectiveDateTime": date,
            "component": [
                {"code": {"coding": [_coding(LOINC, *BP_SYS)]}, "valueQuantity": {"value": v["bp_systolic"], "unit": "mm[Hg]", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"}},
                {"code": {"coding": [_coding(LOINC, *BP_DIA)]}, "valueQuantity": {"value": v["bp_diastolic"], "unit": "mm[Hg]", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"}}]}))
    return out


def lab_observation(l, s):
    val = {}
    try:
        val = {"valueQuantity": {"value": float(str(l["value"]).split()[0]), "unit": l.get("unit") or None}}
    except (ValueError, IndexError, KeyError):
        val = {"valueString": str(l.get("value"))}
    return {"resourceType": "Observation", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [_coding(LOINC, l.get("loinc"))], "text": l["name"]},
            "subject": ref("Patient", s["patient"]["id"]), "effectiveDateTime": l.get("date"), **val}


def care_plan_resource(c, s):
    return {"resourceType": "CarePlan", "status": {"Active": "active", "Resolved": "completed", "Discontinued": "revoked"}.get(c.get("status"), "active"),
            "intent": "plan", "subject": ref("Patient", s["patient"]["id"]), "title": c["problem"],
            "description": c.get("goal"), "activity": [{"detail": {"status": "in-progress", "description": c["interventions"]}}] if c.get("interventions") else None}


def encounter_resource(e, s):
    cls = {"Inpatient": "IMP", "Outpatient": "AMB"}.get(e.get("type"), "AMB")
    return {"resourceType": "Encounter", "status": "finished", "class": {"system": V3_ACT, "code": cls},
            "subject": ref("Patient", s["patient"]["id"]), "period": {"start": e.get("date"), "end": e.get("end")},
            "reasonCode": [{"text": e["reason"]}] if e.get("reason") else None,
            "serviceProvider": ref("Organization", s["facility"]["id"])}


def immunization_resource(i, s):
    return {"resourceType": "Immunization", "status": "completed", "vaccineCode": {"text": i["vaccine"]},
            "patient": ref("Patient", s["patient"]["id"]), "occurrenceDateTime": i["date"], "lotNumber": i.get("batch"),
            "protocolApplied": [{"doseNumberPositiveInt": i["dose"]}] if i.get("dose") else None}


def build_document_bundle(s, identifier_base):
    """Clinical summary as a FHIR document Bundle (Composition + resources)."""
    s = dict(s)
    s["_base"] = identifier_base
    entries, sections = [], []

    def add(kind, key, res):
        e = _entry(kind, key, res)
        entries.append(e)
        return {"reference": e["fullUrl"]}

    add_pat = _entry("Patient", s["patient"]["id"], patient_resource(s, identifier_base))
    add_org = _entry("Organization", s["facility"]["id"], organization_resource(s, identifier_base))
    entries += [add_pat, add_org]

    problem_refs = [add("Condition", c["id"], condition_resource(c, s)) for c in s.get("problems", [])]
    allergy_refs = [add("Allergy", a["id"], allergy_resource(a, s)) for a in s.get("allergies", [])]
    med_refs = [add("MedicationRequest", m["id"], med_request_resource(m, s)) for m in s.get("medications", [])]
    rx_refs = []
    for rx in s.get("prescriptions", []):
        for i, it in enumerate(rx.get("items", [])):
            rx_refs.append(add("Rx", f"{rx['id']}:{i}", med_request_resource(
                {**it, "status": "Active", "start": rx.get("date")}, s)))
    obs_refs = []
    for v in s.get("vitals", []):
        for kind, key, res in vitals_observations(v, s):
            obs_refs.append(add(kind, key, res))
    lab_refs = [add("Lab", l["id"], lab_observation(l, s)) for l in s.get("labs", [])]
    cp_refs = [add("CarePlan", i, care_plan_resource(c, s)) for i, c in enumerate(s.get("care_plans", []))]
    enc_refs = [add("Encounter", e["id"], encounter_resource(e, s)) for e in s.get("encounters", [])]
    imm_refs = [add("Immunization", i["id"], immunization_resource(i, s)) for i in s.get("immunizations", [])]

    def sec(title, code, refs, empty_reason=None):
        d = {"title": title, "code": {"coding": [_coding(LOINC, code)]}}
        if refs:
            d["entry"] = refs
        else:
            d["emptyReason"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/list-empty-reason", "code": empty_reason or "unavailable"}]}
        return d

    sections = [
        sec("Problem list", "11450-4", problem_refs, "nilknown" if s.get("problems_checked") else "unavailable"),
        sec("Allergies and intolerances", "48765-2", allergy_refs,
            "nilknown" if s.get("allergy_status") == "No known allergies" else "unavailable"),
        sec("Medications", "10160-0", med_refs + rx_refs),
        sec("Vital signs", "8716-3", obs_refs),
        sec("Results", "30954-2", lab_refs),
        sec("Plan of care", "18776-5", cp_refs),
        sec("Encounters", "46240-8", enc_refs),
        sec("Immunizations", "11369-6", imm_refs),
    ]
    sections = [x for x in sections if "entry" in x or x["title"] in ("Problem list", "Allergies and intolerances")]
    comp = {
        "resourceType": "Composition", "status": "final",
        "type": {"coding": [_coding(LOINC, "60591-5", "Patient summary Document")]},
        "subject": ref("Patient", s["patient"]["id"]), "date": s["generated_at"],
        "author": [ref("Organization", s["facility"]["id"])], "title": "Patient clinical summary",
        "custodian": ref("Organization", s["facility"]["id"]), "section": sections,
    }
    comp_entry = _entry("Composition", f"{s['patient']['id']}:{s['generated_at']}", comp)
    bundle = {"resourceType": "Bundle", "type": "document", "identifier": {"system": _id_system(identifier_base, "bundle"),
                                                                            "value": rid("Bundle", f"{s['patient']['id']}:{s['generated_at']}")},
              "timestamp": s["generated_at"], "entry": [comp_entry] + entries}
    return bundle


def summarize_bundle(bundle):
    counts = {}
    for e in bundle.get("entry", []):
        t = (e.get("resource") or {}).get("resourceType", "?")
        counts[t] = counts.get(t, 0) + 1
    return f"Bundle({bundle.get('type')}): " + ", ".join(f"{n} {t}" for t, n in sorted(counts.items()))


def validate_bundle(bundle):
    """Structural sanity checks. Not a substitute for DHA's FHIR validator."""
    errors = []
    if bundle.get("resourceType") != "Bundle":
        errors.append("resourceType must be Bundle")
    entries = bundle.get("entry") or []
    if not entries:
        errors.append("Bundle has no entries")
    urls = {e.get("fullUrl") for e in entries}
    if bundle.get("type") == "document" and (not entries or entries[0]["resource"].get("resourceType") != "Composition"):
        errors.append("A document Bundle must start with a Composition")
    for e in entries:
        r = e.get("resource") or {}
        if not r.get("resourceType"):
            errors.append("entry without resourceType")
        for key in ("subject", "patient"):
            rf = (r.get(key) or {}).get("reference")
            if rf and rf.startswith("urn:uuid:") and rf not in urls:
                errors.append(f"{r.get('resourceType')} references missing {rf}")
    return errors


def extract_identifiers(bundle):
    """Identifier values found on Patient resources in an inbound bundle (for matching)."""
    out = []
    for e in bundle.get("entry", []):
        r = e.get("resource") or {}
        if r.get("resourceType") == "Patient":
            for i in r.get("identifier", []):
                if i.get("value"):
                    out.append({"type": (i.get("type") or {}).get("text"), "system": i.get("system"), "value": i["value"]})
    return out
