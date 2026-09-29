"""Maternal & child health encounter schemas and validation."""
import datetime
import json

# field: (label, kind, required). kind: text|number|date|bool|choice:a/b/c
MCH_SCHEMAS = {
    "ANC": {
        "gestation_weeks": ("Gestation (weeks)", "number", False),
        "lmp": ("Last menstrual period", "date", False),
        "edd": ("Expected delivery date", "date", False),
        "gravida": ("Gravida", "number", False),
        "parity": ("Parity", "number", False),
        "anc_visit_number": ("ANC visit number", "number", True),
        "bp_systolic": ("BP systolic", "number", False),
        "bp_diastolic": ("BP diastolic", "number", False),
        "fundal_height_cm": ("Fundal height (cm)", "number", False),
        "fetal_heart_rate": ("Fetal heart rate", "number", False),
        "presentation": ("Presentation", "choice:Cephalic/Breech/Transverse/Not applicable", False),
        "hiv_status": ("HIV status", "choice:Positive/Negative/Unknown", False),
        "syphilis_tested": ("Syphilis tested", "bool", False),
        "hb_g_dl": ("Haemoglobin (g/dL)", "number", False),
        "iron_folate_given": ("Iron/folate given", "bool", False),
        "ipt_sp_dose": ("Malaria IPT (SP) dose number", "number", False),
        "tt_dose": ("Tetanus toxoid dose", "number", False),
        "danger_signs": ("Danger signs noted", "text", False),
    },
    "PNC": {
        "days_postpartum": ("Days postpartum", "number", True),
        "mode_of_delivery": ("Mode of delivery", "choice:SVD/Caesarean/Assisted/Unknown", False),
        "bp_systolic": ("BP systolic", "number", False),
        "bp_diastolic": ("BP diastolic", "number", False),
        "uterus_involution": ("Uterine involution normal", "bool", False),
        "lochia": ("Lochia", "text", False),
        "breastfeeding": ("Breastfeeding", "choice:Exclusive/Mixed/None", False),
        "baby_weight_kg": ("Baby weight (kg)", "number", False),
        "danger_signs": ("Danger signs noted", "text", False),
    },
    "Child Welfare": {
        "feeding": ("Feeding", "choice:Exclusive breastfeeding/Complementary/Mixed/Other", False),
        "development_milestones_met": ("Milestones met for age", "bool", False),
        "vitamin_a_given": ("Vitamin A given", "bool", False),
        "deworming_given": ("Deworming given", "bool", False),
        "growth_monitoring_done": ("Growth monitoring done", "bool", False),
        "danger_signs": ("Danger signs noted", "text", False),
    },
    "Family Planning": {
        "method": ("Method", "choice:Pill/Injectable/Implant/IUCD/Condom/Sterilisation/Natural/None", True),
        "new_or_revisit": ("Client type", "choice:New/Revisit", False),
        "bp_systolic": ("BP systolic", "number", False),
        "bp_diastolic": ("BP diastolic", "number", False),
        "counselled": ("Counselled", "bool", False),
    },
    "Delivery": {
        "delivery_date": ("Delivery date", "date", True),
        "mode_of_delivery": ("Mode of delivery", "choice:SVD/Caesarean/Assisted/Unknown", True),
        "outcome": ("Outcome", "choice:Live birth/Stillbirth/Abortion", True),
        "birth_weight_kg": ("Birth weight (kg)", "number", False),
        "apgar_1": ("Apgar 1 min", "number", False),
        "apgar_5": ("Apgar 5 min", "number", False),
        "complications": ("Complications", "text", False),
    },
}


class MchValidationError(ValueError):
    pass


def validate(encounter_type, raw):
    """Returns cleaned dict; raises MchValidationError with a readable message."""
    schema = MCH_SCHEMAS.get(encounter_type)
    if schema is None:
        raise MchValidationError("Unknown MCH encounter type.")
    clean = {}
    for key, (label, kind, required) in schema.items():
        v = raw.get(key)
        if v in (None, ""):
            if required:
                raise MchValidationError(f"{label} is required.")
            continue
        if kind == "number":
            try:
                clean[key] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                raise MchValidationError(f"{label} must be a number.")
            if clean[key] < 0:
                raise MchValidationError(f"{label} cannot be negative.")
        elif kind == "date":
            try:
                clean[key] = datetime.date.fromisoformat(str(v)).isoformat()
            except ValueError:
                raise MchValidationError(f"{label} must be a valid date.")
        elif kind == "bool":
            clean[key] = str(v).lower() in ("1", "true", "yes", "on")
        elif kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split("/")
            if v not in options:
                raise MchValidationError(f"{label}: choose one of {', '.join(options)}.")
            clean[key] = v
        else:
            clean[key] = str(v)[:300]
    return clean


def dumps(clean):
    return json.dumps(clean, sort_keys=True)


def loads(text):
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


def is_pregnant_from(encounters, today=None):
    """True if the latest ANC encounter is recent and no later delivery is recorded.
    `encounters`: iterable of (type, date, data_dict)."""
    today = today or datetime.date.today()
    anc = [e for e in encounters if e[0] == "ANC"]
    if not anc:
        return False
    last_anc = max(e[1] for e in anc)
    if (today - last_anc).days > 300:
        return False
    return not any(e[0] == "Delivery" and e[1] >= last_anc for e in encounters)


# Kenya routine immunization schedule (KEPI) — reference for "due" prompts.
# Confirm against the current MOH schedule before relying on due dates.
KEPI_SCHEDULE = [
    ("BCG", 0, 0), ("OPV 0", 0, 0), ("OPV 1", 6, 1), ("PCV 1", 6, 1), ("Penta 1", 6, 1), ("Rota 1", 6, 1),
    ("OPV 2", 10, 2), ("PCV 2", 10, 2), ("Penta 2", 10, 2), ("Rota 2", 10, 2),
    ("OPV 3", 14, 3), ("PCV 3", 14, 3), ("Penta 3", 14, 3), ("IPV", 14, 1),
    ("Measles-Rubella 1", 39, 1), ("Measles-Rubella 2", 78, 2),
]  # (vaccine, age in weeks, dose)


def due_vaccines(dob, given_names, today=None):
    today = today or datetime.date.today()
    weeks = (today - dob).days // 7
    given = {g.strip().lower() for g in given_names}
    return [v for v, wk, _ in KEPI_SCHEDULE if wk <= weeks and v.lower() not in given]
