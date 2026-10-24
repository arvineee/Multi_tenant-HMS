"""
Defines what each hospital level (per Kenya's KEPH facility-level
framework) is officially allowed to do. Enforced server-side everywhere
it matters — not just hidden in the UI — so a Level 2 dispensary can
never admit an inpatient or order a CT scan even via a crafted request,
regardless of what the client sends.
"""

LEVEL_CAPABILITIES = {
    "Level 1": {"inpatient": False, "radiology_modalities": []},
    "Level 2": {"inpatient": False, "radiology_modalities": ["X-Ray"]},
    "Level 3": {"inpatient": True, "radiology_modalities": ["X-Ray", "Ultrasound"]},
    "Level 4": {"inpatient": True, "radiology_modalities": ["X-Ray", "Ultrasound", "CT"]},
    "Level 5": {"inpatient": True, "radiology_modalities": ["X-Ray", "Ultrasound", "CT", "MRI", "Mammography", "Fluoroscopy"]},
    "Level 6": {"inpatient": True, "radiology_modalities": ["X-Ray", "Ultrasound", "CT", "MRI", "Mammography", "Fluoroscopy", "Other"]},
}


# Small facilities (Level 1 community units, Level 2 dispensaries/clinics) are
# often run by one or two clinicians, so a Doctor there has to carry a patient
# through the whole visit: register -> triage -> consult -> lab / X-ray ->
# dispense -> bill. At Level 3 and above these duties stay separated between
# roles, so the extras below apply ONLY while the user's own hospital is Level
# 1 or 2 — nothing is stored on the role, and moving the doctor to a bigger
# facility removes them immediately.
SMALL_FACILITY_LEVELS = ("Level 1", "Level 2")
SMALL_FACILITY_EXTRA_PERMISSIONS = {
    "Doctor": frozenset({
        "triage.create",
        "lab.result",
        "radiology.report",
        "pharmacy.dispense",
        "billing.manage",
    }),
}


def small_facility_extra_permissions(role_name, hospital):
    """Permissions a role gains only because it works at a Level 1/2 facility."""
    if not hospital or hospital.level not in SMALL_FACILITY_LEVELS:
        return frozenset()
    return SMALL_FACILITY_EXTRA_PERMISSIONS.get(role_name, frozenset())


def hospital_allows_inpatient(hospital):
    if not hospital:
        return False
    return LEVEL_CAPABILITIES.get(hospital.level, {}).get("inpatient", False)


def hospital_allowed_radiology_modalities(hospital):
    if not hospital:
        return []
    return LEVEL_CAPABILITIES.get(hospital.level, {}).get("radiology_modalities", [])


def radiology_test_allowed(hospital, radiology_test):
    return radiology_test.modality in hospital_allowed_radiology_modalities(hospital)
