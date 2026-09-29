from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import Visit, log_action
from app.security.access import load_patient
from app.cds import service as svc, rules
from app.cds.models import CdsRuleSetting

cds_bp = Blueprint("cds", __name__, template_folder="../templates/cds")


@cds_bp.route("/cds/check", methods=["POST"])
@login_required
@permission_required("patient.view")
def check():
    """Preview alerts for a draft prescription (called by the prescribe form before saving)."""
    d = request.get_json(silent=True) or {}
    patient = load_patient(int(d.get("patient_id") or 0), section="cds-check")
    visit = db.session.get(Visit, int(d["visit_id"])) if d.get("visit_id") else None
    try:
        drug_ids = [int(x) for x in d.get("drug_ids", [])]
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad drug list."), 400
    alerts = svc.evaluate(patient, visit, drug_ids, current_user.organization_id)
    return jsonify(success=True, alerts=[{"rule": a.rule_id, "severity": a.severity, "message": a.message,
                                          "detail": a.detail, "needs_override": a.requires_override} for a in alerts])


@cds_bp.route("/compliance/decision-support", methods=["GET", "POST"])
@login_required
@permission_required("cds.manage")
def settings():
    org = current_user.organization_id
    if request.method == "POST":
        d = request.get_json(silent=True) or request.form
        rid = d.get("rule_id")
        if rid not in rules.RULE_CATALOG:
            return jsonify(success=False, error="Unknown rule."), 400
        row = CdsRuleSetting.query.filter_by(organization_id=org, rule_id=rid).first() or CdsRuleSetting(organization_id=org, rule_id=rid)
        row.enabled, row.updated_by_id = str(d.get("enabled")).lower() in ("1", "true", "on"), current_user.id
        db.session.add(row)
        log_action(current_user, "update", "CdsRuleSetting", None, {"rule": rid, "enabled": row.enabled})
        db.session.commit()
        return jsonify(success=True)
    off = {s.rule_id for s in CdsRuleSetting.query.filter_by(organization_id=org, enabled=False).all()}
    return render_template("cds/settings.html", catalog=rules.RULE_CATALOG, off=off)
