"""WHO growth-standard z-scores from LMS reference rows.

The reference tables are WHO's published data. They are NOT typed in here:
load them with `python import_growth_reference.py <file.csv>` (columns:
indicator,sex,x,L,M,S). Until they are loaded, z-scores stay empty and the
chart page says so. Formulas follow the WHO Anthro documentation
(z = ((X/M)^L - 1) / (L*S), with the restricted adjustment beyond +-3 SD for
weight-for-age, BMI-for-age and weight-for-length/height).
"""
import math

INDICATORS = {
    "wfa": "Weight-for-age", "lhfa": "Length/height-for-age", "bfa": "BMI-for-age",
    "wfl": "Weight-for-length", "wfh": "Weight-for-height", "hcfa": "Head circumference-for-age",
}
_ADJUSTED = {"wfa", "bfa", "wfl", "wfh"}


def lms_zscore(x, L, M, S, adjust=False):
    if x is None or x <= 0 or M <= 0 or S <= 0:
        return None
    z = math.log(x / M) / S if L == 0 else ((x / M) ** L - 1) / (L * S)
    if adjust and abs(z) > 3:
        def sd(k):
            return M * math.exp(S * k) if L == 0 else M * (1 + L * S * k) ** (1 / L)
        if z > 3:
            z = 3 + (x - sd(3)) / (sd(3) - sd(2))
        else:
            z = -3 + (x - sd(-3)) / (sd(-2) - sd(-3))
    return round(z, 2)


def interpolate_row(rows, x):
    """rows: sorted list of (x, L, M, S). Linear interpolation; None outside range."""
    if not rows or x < rows[0][0] or x > rows[-1][0]:
        return None
    for i, r in enumerate(rows):
        if r[0] == x:
            return r[1:]
        if r[0] > x:
            a, b = rows[i - 1], r
            t = (x - a[0]) / (b[0] - a[0])
            return tuple(a[k] + t * (b[k] - a[k]) for k in (1, 2, 3))
    return None


def zscore_for(indicator, value, x, rows):
    """rows: sorted [(x, L, M, S), ...] for this indicator and sex."""
    row = interpolate_row(rows, x)
    if row is None:
        return None
    return lms_zscore(value, *row, adjust=indicator in _ADJUSTED)


def classify(indicator, z):
    """Plain-language WHO cut-offs."""
    if z is None:
        return None
    if indicator == "wfa":
        return ("Severely underweight" if z < -3 else "Underweight" if z < -2 else
                "Normal" if z <= 2 else "Possible overweight (check weight-for-height)")
    if indicator in ("lhfa",):
        return "Severely stunted" if z < -3 else "Stunted" if z < -2 else "Normal"
    if indicator in ("wfl", "wfh", "bfa"):
        return ("Severe acute malnutrition" if z < -3 else "Moderate acute malnutrition" if z < -2 else
                "Normal" if z <= 1 else "Possible risk of overweight" if z <= 2 else
                "Overweight" if z <= 3 else "Obese")
    if indicator == "hcfa":
        return "Microcephaly range" if z < -2 else "Macrocephaly range" if z > 2 else "Normal"
    return None


def muac_class(muac_cm, age_months):
    """WHO/MOH MUAC bands for children 6-59 months."""
    if muac_cm is None or age_months is None or not (6 <= age_months <= 59):
        return None
    return "Severe acute malnutrition" if muac_cm < 11.5 else "Moderate acute malnutrition" if muac_cm < 12.5 else "Normal"


def compute_for(measure, sex, rows_lookup):
    """Fill z-scores on a GrowthMeasurement-like object. rows_lookup(indicator, sex) -> sorted rows.
    Uses length/height-for-age for x=age_days, and weight-for-length (<24 mo) or height (>=24 mo) by cm."""
    if measure.age_days is None or sex not in ("M", "F"):
        return measure
    age = measure.age_days
    if measure.weight_kg:
        measure.waz = zscore_for("wfa", measure.weight_kg, age, rows_lookup("wfa", sex))
    if measure.height_cm:
        measure.haz = zscore_for("lhfa", measure.height_cm, age, rows_lookup("lhfa", sex))
    if measure.bmi:
        measure.baz = zscore_for("bfa", measure.bmi, age, rows_lookup("bfa", sex))
    if measure.weight_kg and measure.height_cm:
        ind = "wfl" if age < 730 else "wfh"
        measure.whz = zscore_for(ind, measure.weight_kg, measure.height_cm, rows_lookup(ind, sex))
    if measure.head_circumference_cm:
        measure.hcz = zscore_for("hcfa", measure.head_circumference_cm, age, rows_lookup("hcfa", sex))
    return measure
