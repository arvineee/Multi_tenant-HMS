"""Terminology mapping (ICD-11 / SNOMED CT / LOINC) and the HPT registry cache."""
import csv
import io
import json

from app.extensions import db
from app.models import DiagnosisCode, LabTest, RadiologyTest, Drug
from app.terminology.models import HptProduct

TARGETS = {
    "diagnosis": (DiagnosisCode, "code", {"icd11_code", "snomed_ct_code"}, False),
    "lab": (LabTest, "code", {"loinc_code", "snomed_ct_code"}, True),
    "radiology": (RadiologyTest, "code", {"loinc_code", "snomed_ct_code"}, True),
    "drug": (Drug, "id", {"hpt_code", "atc_code"}, True),
}


def import_mapping_csv(kind, text, organization_id=None):
    """CSV: first column = the local key (code, or drug id/name), other columns = mapping fields.
    Returns (updated, errors)."""
    model, key, allowed, org_scoped = TARGETS[kind]
    rd = csv.DictReader(io.StringIO(text))
    if not rd.fieldnames:
        return 0, ["Empty file."]
    keycol = rd.fieldnames[0]
    fields = [f for f in rd.fieldnames[1:] if f in allowed]
    if not fields:
        return 0, [f"No mappable columns. Allowed: {', '.join(sorted(allowed))}."]
    n, errors = 0, []
    for i, row in enumerate(rd, start=2):
        k = (row.get(keycol) or "").strip()
        q = model.query
        if org_scoped:
            q = q.filter_by(organization_id=organization_id)
        if kind == "drug":
            obj = q.filter_by(id=int(k)).first() if k.isdigit() else q.filter(Drug.name.ilike(k)).first()
        else:
            obj = q.filter_by(**{key: k}).first()
        if not obj:
            errors.append(f"Row {i}: no match for {k!r}")
            continue
        for f in fields:
            v = (row.get(f) or "").strip()
            if v:
                setattr(obj, f, v[:50])
        n += 1
    return n, errors


def coverage(organization_id):
    """How much of each catalog is mapped — feeds the compliance dashboard."""
    def pct(q_all, q_mapped):
        total = q_all.count()
        return (round(100.0 * q_mapped.count() / total, 1) if total else None, total)
    return {
        "ICD-11": pct(DiagnosisCode.query, DiagnosisCode.query.filter(DiagnosisCode.icd11_code.isnot(None))),
        "SNOMED CT (diagnosis)": pct(DiagnosisCode.query, DiagnosisCode.query.filter(DiagnosisCode.snomed_ct_code.isnot(None))),
        "LOINC (lab)": pct(LabTest.query.filter_by(organization_id=organization_id),
                           LabTest.query.filter(LabTest.organization_id == organization_id, LabTest.loinc_code.isnot(None))),
        "HPT (drugs)": pct(Drug.query.filter_by(organization_id=organization_id),
                           Drug.query.filter(Drug.organization_id == organization_id, Drug.hpt_code.isnot(None))),
    }


def hpt_search(cfg, q, transport=None):
    """Live search in the national HPT registry; falls back to the local cache when unset."""
    if cfg.get("HPT_REGISTRY_URL"):
        import requests
        h = {"Accept": "application/json"}
        if cfg.get("HPT_REGISTRY_TOKEN"):
            h["Authorization"] = "Bearer " + cfg["HPT_REGISTRY_TOKEN"]
        r = (transport or requests).get(cfg["HPT_REGISTRY_URL"] + cfg.get("HPT_SEARCH_PATH", "").format(q=q), headers=h, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"HPT registry returned {r.status_code}")
        data = r.json()
        items = data if isinstance(data, list) else data.get("results", data.get("data", []))
        for it in items:
            code = str(it.get("code") or it.get("hpt_code") or it.get("id") or "")
            if not code:
                continue
            row = HptProduct.query.filter_by(hpt_code=code).first() or HptProduct(hpt_code=code, name="")
            row.name = it.get("name") or row.name or code
            row.generic_name, row.form, row.strength = it.get("generic_name"), it.get("form"), it.get("strength")
            row.atc_code, row.status = it.get("atc_code"), it.get("status")
            db.session.add(row)
        db.session.flush()
    like = f"%{q}%"
    return HptProduct.query.filter(db.or_(HptProduct.name.ilike(like), HptProduct.generic_name.ilike(like),
                                          HptProduct.hpt_code.ilike(like))).limit(25).all()
