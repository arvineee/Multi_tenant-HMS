"""Pure helpers for surveillance and quality reporting (no database)."""
import datetime
import xml.etree.ElementTree as ET

# (code, name, category, icd10 prefixes, MOH form)
# STARTER LIST based on Kenya's IDSR priority diseases. Confirm against the
# current MOH IDSR technical guidelines / circulars and edit from the
# Surveillance -> Disease list page: it is a policy list, not a code constant.
DEFAULT_NOTIFIABLE = [
    ("CHOLERA", "Cholera", "immediate", "A00", "MOH 502"),
    ("PLAGUE", "Plague", "immediate", "A20", "MOH 502"),
    ("ANTHRAX", "Anthrax", "immediate", "A22", "MOH 502"),
    ("VHF", "Viral haemorrhagic fevers (Ebola, Marburg, Lassa, CCHF, Rift Valley)", "immediate",
     "A98.0,A98.3,A98.4,A96,A92.4,A99", "MOH 502"),
    ("YELLOWFEVER", "Yellow fever", "immediate", "A95", "MOH 502"),
    ("MEASLES", "Measles", "immediate", "B05", "MOH 502"),
    ("AFP", "Acute flaccid paralysis / Poliomyelitis", "immediate", "A80,G82.0", "MOH 502"),
    ("MENINGITIS", "Meningococcal meningitis", "immediate", "A39,G00.9", "MOH 502"),
    ("RABIES", "Rabies (human)", "immediate", "A82", "MOH 502"),
    ("NNT", "Neonatal tetanus", "immediate", "A33", "MOH 502"),
    ("MPOX", "Mpox", "immediate", "B04", "MOH 502"),
    ("DIPHTHERIA", "Diphtheria", "immediate", "A36", "MOH 502"),
    ("COVID19", "COVID-19", "immediate", "U07.1,U07.2", "MOH 502"),
    ("MALARIA", "Malaria", "weekly", "B50,B51,B52,B53,B54", "MOH 505"),
    ("DIARRHOEA", "Diarrhoea (acute)", "weekly", "A09", "MOH 505"),
    ("DYSENTERY", "Dysentery (bloody diarrhoea)", "weekly", "A03", "MOH 505"),
    ("TYPHOID", "Typhoid fever", "weekly", "A01", "MOH 505"),
    ("PNEUMONIA", "Pneumonia", "weekly", "J12,J13,J14,J15,J16,J17,J18", "MOH 505"),
    ("TB", "Tuberculosis", "weekly", "A15,A16,A17,A18,A19", "MOH 505"),
    ("HIV", "HIV disease", "weekly", "B20,B21,B22,B23,B24,Z21", "MOH 505"),
    ("SARI", "Influenza / severe acute respiratory infection", "weekly", "J09,J10,J11", "MOH 505"),
    ("PERTUSSIS", "Pertussis", "weekly", "A37", "MOH 505"),
    ("TETANUS", "Tetanus (non-neonatal)", "weekly", "A35", "MOH 505"),
    ("RVF", "Rift Valley fever", "weekly", "A92.4", "MOH 505"),
]


def normalise_code(code):
    return (code or "").upper().replace(".", "").strip()


def match_disease(diagnosis_code, diseases):
    """diseases: iterable of objects with .prefixes and .is_active. Returns the
    most specific (longest-prefix) match or None."""
    c = normalise_code(diagnosis_code)
    best, best_len = None, 0
    for d in diseases:
        if not d.is_active:
            continue
        for p in d.prefixes:
            if c.startswith(p) and len(p) > best_len:
                best, best_len = d, len(p)
    return best


def epi_week(day):
    """ISO epidemiological week (Monday-Sunday). Returns (year, week, start, end).
    Confirm with your county surveillance office if they use a different week convention."""
    y, w, wd = day.isocalendar()
    start = day - datetime.timedelta(days=wd - 1)
    return y, w, start, start + datetime.timedelta(days=6)


def previous_epi_week(today=None):
    today = today or datetime.date.today()
    return epi_week(today - datetime.timedelta(days=7))


AGE_BANDS = [("<5y", 0, 4), ("5y+", 5, 200)]  # MOH 505 splits under-5 / over-5


def age_band(age_years):
    if age_years is None:
        return "unknown"
    return "<5y" if age_years < 5 else "5y+"


def aggregate_weekly(cases):
    """cases: iterable of dicts {disease_code, age_years, outcome}. Returns
    {code: {'<5y': {'cases': n, 'deaths': n}, '5y+': {...}, 'unknown': {...}}}."""
    out = {}
    for c in cases:
        slot = out.setdefault(c["disease_code"], {})
        b = slot.setdefault(age_band(c.get("age_years")), {"cases": 0, "deaths": 0})
        b["cases"] += 1
        if (c.get("outcome") or "").lower() == "died":
            b["deaths"] += 1
    return out


def hours_between(a, b):
    return round((b - a).total_seconds() / 3600, 1)


# --- SDMX-ML (simplified generic data message) ------------------------------
# Namespaces for SDMX 2.1 generic data. DHA/KHIS may require a specific Data
# Structure Definition id/agency; set them via the arguments below.
_NS_MSG = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
_NS_GEN = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"
_NS_COM = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"


def build_sdmx_generic(dataset_id, agency, structure_id, series, sender="MEDICORE", prepared=None):
    """series: list of {'dims': {'ORG_UNIT':..,'PERIOD':..,'INDICATOR':..}, 'value': n}. Returns bytes."""
    prepared = prepared or datetime.datetime.utcnow().replace(microsecond=0)
    ET.register_namespace("message", _NS_MSG)
    ET.register_namespace("generic", _NS_GEN)
    ET.register_namespace("common", _NS_COM)
    root = ET.Element(f"{{{_NS_MSG}}}GenericData")
    hdr = ET.SubElement(root, f"{{{_NS_MSG}}}Header")
    ET.SubElement(hdr, f"{{{_NS_MSG}}}ID").text = f"MC-{dataset_id}-{int(prepared.timestamp())}"
    ET.SubElement(hdr, f"{{{_NS_MSG}}}Test").text = "false"
    ET.SubElement(hdr, f"{{{_NS_MSG}}}Prepared").text = prepared.isoformat()
    ET.SubElement(ET.SubElement(hdr, f"{{{_NS_MSG}}}Sender"), "ID").text = sender
    st = ET.SubElement(hdr, f"{{{_NS_MSG}}}Structure", structureID=structure_id, dimensionAtObservation="AllDimensions")
    ref = ET.SubElement(ET.SubElement(st, f"{{{_NS_COM}}}Structure"), "Ref", id=structure_id, agencyID=agency, version="1.0")
    ds = ET.SubElement(root, f"{{{_NS_MSG}}}DataSet")
    for s in series:
        obs = ET.SubElement(ds, f"{{{_NS_GEN}}}Obs")
        key = ET.SubElement(obs, f"{{{_NS_GEN}}}ObsKey")
        for k, v in s["dims"].items():
            ET.SubElement(key, f"{{{_NS_GEN}}}Value", id=k, value=str(v))
        ET.SubElement(obs, f"{{{_NS_GEN}}}ObsValue", value=str(s["value"]))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def weekly_to_series(payload, mfl_or_id, period_label):
    series = []
    for code, bands in sorted(payload.items()):
        for band, v in sorted(bands.items()):
            for measure in ("cases", "deaths"):
                series.append({"dims": {"ORG_UNIT": mfl_or_id, "PERIOD": period_label, "INDICATOR": f"{code}_{measure.upper()}",
                                        "AGE_GROUP": band}, "value": v[measure]})
    return series


def dhis2_data_value_set(payload, org_unit, period_iso_week, data_set=None):
    """DHIS2-style dataValueSets body. Data element ids must be mapped to the
    receiving system's UIDs; here the element is our code, ready for mapping."""
    vals = []
    for code, bands in sorted(payload.items()):
        for band, v in sorted(bands.items()):
            vals.append({"dataElement": f"{code}_CASES_{band}", "value": str(v["cases"])})
            vals.append({"dataElement": f"{code}_DEATHS_{band}", "value": str(v["deaths"])})
    body = {"orgUnit": org_unit, "period": period_iso_week, "dataValues": vals}
    if data_set:
        body["dataSet"] = data_set
    return body
