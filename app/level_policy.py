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
