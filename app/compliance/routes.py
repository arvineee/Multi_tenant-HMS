from flask import Blueprint, render_template, request, jsonify, abort, redirect, url_for
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import log_action
from app.compliance import service as svc
from app.security.models import ComplianceAttestation

compliance_bp = Blueprint("compliance", __name__, template_folder="../templates/compliance")


@compliance_bp.route("/compliance")
@login_required
@permission_required("compliance.view")
def dashboard():
    org = current_user.organization
    sections, overall = svc.score(svc.evaluate(org, is_secure_request=request.is_secure))
    att = {a.item_key: a for a in ComplianceAttestation.query.filter_by(organization_id=org.id).all()}
    return render_template("compliance/dashboard.html", sections=sections, overall=overall, attestations=svc.ATTESTATIONS,
                           att=att, can_attest=current_user.has_permission("security.manage"))


@compliance_bp.route("/compliance/attest", methods=["POST"])
@login_required
@permission_required("security.manage")
def attest():
    d = request.get_json(silent=True) or request.form
    key = d.get("item_key")
    if key not in svc.ATTESTATIONS:
        return jsonify(success=False, error="Unknown item."), 400
    row = ComplianceAttestation.query.filter_by(organization_id=current_user.organization_id, item_key=key).first()
    if not row:
        row = ComplianceAttestation(organization_id=current_user.organization_id, item_key=key)
        db.session.add(row)
    row.value = str(d.get("value")).lower() in ("1", "true", "on")
    row.note, row.attested_by_id = (d.get("note") or "")[:500] or None, current_user.id
    log_action(current_user, "attest", "ComplianceAttestation", None, {"item": key, "value": row.value})
    db.session.commit()
    return jsonify(success=True)
