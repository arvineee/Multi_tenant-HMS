"""Clinical decision support rules. Pure functions over plain data so they can
be tested without a database.

IMPORTANT: this is a rule engine with a small, conservative starter rule set,
not a drug-interaction database. Drug-drug interaction checking needs a
licensed/curated interaction dataset; none is bundled and none is invented
here. Rule content (thresholds, allergy classes) must be reviewed and signed
off by your clinical lead before go-live; each rule can be switched off per
organization under Compliance -> Decision support.
"""
from dataclasses import dataclass, field

SEVERITIES = ["info", "warning", "critical"]


@dataclass
class Alert:
    rule_id: str
    severity: str
    message: str
    detail: str = ""
    requires_override: bool = False  # clinician must give a reason to proceed


@dataclass
class DrugInfo:
    id: int
    name: str
    generic_name: str = ""
    atc_code: str = ""
    hpt_code: str = ""
    pregnancy_caution: bool = False
    min_age_months: int = None


@dataclass
class Context:
    age_months: int = None
    sex: str = None
    pregnant: bool = False
    allergies: list = field(default_factory=list)        # dicts: allergen_name, drug_id, severity, status
    active_medications: list = field(default_factory=list)  # dicts: drug_id, name, generic_name
    problems: list = field(default_factory=list)          # dicts: description, code, status, is_chronic
    vitals: dict = field(default_factory=dict)            # temperature_c, pulse_bpm, bp_systolic, bp_diastolic, respiratory_rate, spo2_percent
    labs: list = field(default_factory=list)              # dicts: name, value, critical_low/high, ref_low/high


# Conservative allergy groups: an allergy to any member flags the others as a
# POSSIBLE cross-reaction (warning), while an exact match is critical.
ALLERGY_GROUPS = {
    "penicillins": ["penicillin", "amoxicillin", "ampicillin", "flucloxacillin", "cloxacillin",
                    "piperacillin", "amoxicillin-clavulanate", "co-amoxiclav", "benzylpenicillin", "phenoxymethylpenicillin"],
    "sulfonamides": ["sulfamethoxazole", "cotrimoxazole", "co-trimoxazole", "sulfadoxine", "sulfadiazine", "sulfonamide"],
    "nsaids": ["ibuprofen", "diclofenac", "naproxen", "aspirin", "acetylsalicylic", "ketorolac", "indomethacin", "mefenamic"],
}


def _norm(s):
    return (s or "").strip().lower()


def _terms(d):
    return {t for t in (_norm(d.name), _norm(d.generic_name)) if t}


def _group_of(term):
    for g, members in ALLERGY_GROUPS.items():
        if any(m in term or term in m for m in members if term):
            return g
    return None


def check_allergy(drug, ctx):
    out = []
    dterms = _terms(drug)
    dgroups = {g for g in (_group_of(t) for t in dterms) if g}
    for a in ctx.allergies:
        if _norm(a.get("status", "Active")) not in ("active", ""):
            continue
        name = _norm(a.get("allergen_name"))
        exact = (a.get("drug_id") is not None and a.get("drug_id") == drug.id) or any(
            name and (name in t or t in name) for t in dterms)
        if exact:
            out.append(Alert("allergy_conflict", "critical",
                             f"{drug.name}: patient has a recorded allergy to {a.get('allergen_name')}.",
                             f"Reaction: {a.get('reaction') or 'not recorded'}; severity: {a.get('severity') or 'not recorded'}.",
                             requires_override=True))
            continue
        ag = _group_of(name)
        if ag and ag in dgroups:
            out.append(Alert("allergy_cross_reaction", "warning",
                             f"{drug.name} is in the same group ({ag}) as the recorded allergy to {a.get('allergen_name')}.",
                             "Possible cross-reactivity; confirm before prescribing.", requires_override=True))
    return out


def check_duplicate(drug, ctx, pending_ids=()):
    dterms = _terms(drug)
    out = []
    for m in ctx.active_medications:
        mt = {_norm(m.get("name")), _norm(m.get("generic_name"))} - {""}
        if m.get("drug_id") == drug.id or (dterms & mt):
            out.append(Alert("duplicate_therapy", "warning",
                             f"{drug.name}: patient already has this medicine on their active list ({m.get('name')}).",
                             "Confirm this is a deliberate repeat, not a duplicate.", requires_override=True))
            break
    if list(pending_ids).count(drug.id) > 1:
        out.append(Alert("duplicate_therapy", "warning", f"{drug.name} appears more than once in this prescription."))
    return out


def check_pregnancy(drug, ctx):
    if ctx.pregnant and drug.pregnancy_caution:
        return [Alert("pregnancy_caution", "critical",
                      f"{drug.name} is flagged for caution in pregnancy, and an active antenatal record exists.",
                      "Review risk/benefit or choose an alternative.", requires_override=True)]
    return []


def check_age(drug, ctx):
    if drug.min_age_months is not None and ctx.age_months is not None and ctx.age_months < drug.min_age_months:
        return [Alert("pediatric_age", "critical",
                      f"{drug.name} is flagged as not for patients under {drug.min_age_months} months.",
                      f"Patient is {ctx.age_months} months old.", requires_override=True)]
    return []


def check_prescription(drug, ctx, pending_ids=()):
    return (check_allergy(drug, ctx) + check_duplicate(drug, ctx, pending_ids)
            + check_pregnancy(drug, ctx) + check_age(drug, ctx))


def check_lab_result(name, value, ref_low=None, ref_high=None, critical_low=None, critical_high=None, unit=""):
    try:
        v = float(str(value).strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return None
    u = f" {unit}" if unit else ""
    if critical_low is not None and v <= critical_low or critical_high is not None and v >= critical_high:
        return Alert("critical_lab", "critical", f"CRITICAL result: {name} = {v}{u}.",
                     "Notify the treating clinician immediately.")
    if ref_low is not None and v < ref_low or ref_high is not None and v > ref_high:
        return Alert("abnormal_lab", "info", f"Abnormal result: {name} = {v}{u}.",
                     f"Reference range {ref_low if ref_low is not None else '-'} to {ref_high if ref_high is not None else '-'}.")
    return None


def check_vitals(ctx):
    """Screening thresholds for adults/older children; not diagnostic."""
    v, out = ctx.vitals or {}, []
    adult = ctx.age_months is None or ctx.age_months >= 12 * 12
    if v.get("spo2_percent") is not None and v["spo2_percent"] < 90:
        out.append(Alert("abnormal_vitals", "critical", f"SpO2 {v['spo2_percent']}% is below 90%.", "Consider oxygen and urgent review."))
    if v.get("temperature_c") is not None:
        if v["temperature_c"] >= 39.0:
            out.append(Alert("abnormal_vitals", "warning", f"High temperature {v['temperature_c']} °C."))
        elif v["temperature_c"] < 35.0:
            out.append(Alert("abnormal_vitals", "warning", f"Low temperature {v['temperature_c']} °C (hypothermia range)."))
    if adult:
        s, d = v.get("bp_systolic"), v.get("bp_diastolic")
        if (s is not None and s >= 180) or (d is not None and d >= 120):
            out.append(Alert("abnormal_vitals", "critical", f"Severely raised BP {s}/{d}.", "Assess for hypertensive emergency."))
        elif (s is not None and s < 90):
            out.append(Alert("abnormal_vitals", "warning", f"Low systolic BP {s}."))
        if v.get("pulse_bpm") is not None and (v["pulse_bpm"] > 130 or v["pulse_bpm"] < 40):
            out.append(Alert("abnormal_vitals", "warning", f"Pulse {v['pulse_bpm']} bpm is outside 40-130."))
        if v.get("respiratory_rate") is not None and (v["respiratory_rate"] >= 30 or v["respiratory_rate"] < 8):
            out.append(Alert("abnormal_vitals", "warning", f"Respiratory rate {v['respiratory_rate']}/min is outside 8-29."))
    return out


def check_problem_context(ctx):
    """Uses the problem list: flag readings that are off-target for known chronic problems."""
    out, v = [], ctx.vitals or {}
    text = " ".join(_norm(p.get("description")) for p in ctx.problems if _norm(p.get("status", "active")) == "active")
    if "hypertension" in text and v.get("bp_systolic") and v.get("bp_diastolic") \
            and (v["bp_systolic"] >= 140 or v["bp_diastolic"] >= 90):
        out.append(Alert("chronic_review", "info", f"Known hypertension and BP is {v['bp_systolic']}/{v['bp_diastolic']}.",
                         "Review adherence and treatment."))
    return out


def evaluate_all(ctx, drugs=(), enabled=lambda rule_id: True):
    alerts = list(check_vitals(ctx)) + check_problem_context(ctx)
    ids = [d.id for d in drugs]
    for d in drugs:
        alerts += check_prescription(d, ctx, ids)
    for lab in ctx.labs:
        a = check_lab_result(lab.get("name"), lab.get("value"), lab.get("ref_low"), lab.get("ref_high"),
                             lab.get("critical_low"), lab.get("critical_high"), lab.get("unit", ""))
        if a:
            alerts.append(a)
    # de-duplicate identical messages, apply per-rule switches
    seen, final = set(), []
    for a in alerts:
        base = "allergy_conflict" if a.rule_id == "allergy_cross_reaction" else a.rule_id
        if not enabled(a.rule_id) or not enabled(base):
            continue
        key = (a.rule_id, a.message)
        if key not in seen:
            seen.add(key)
            final.append(a)
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(final, key=lambda a: order[a.severity])


RULE_CATALOG = {
    "allergy_conflict": "Allergy conflict (exact match and same-group cross-reaction)",
    "duplicate_therapy": "Duplicate therapy against the active medication list",
    "pregnancy_caution": "Pregnancy caution for flagged medicines (needs an antenatal record)",
    "pediatric_age": "Minimum-age check for flagged medicines",
    "critical_lab": "Critical laboratory values",
    "abnormal_lab": "Abnormal laboratory values",
    "abnormal_vitals": "Abnormal vital signs screening",
    "chronic_review": "Chronic-problem review prompts",
}
