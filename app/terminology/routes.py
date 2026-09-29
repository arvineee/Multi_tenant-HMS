from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import log_action
from app.terminology import service as svc
from app.terminology.models import HptProduct

terminology_bp = Blueprint("terminology", __name__, template_folder="../templates/terminology")


@terminology_bp.route("/terminology")
@login_required
@permission_required("catalogs.manage")
def index():
    return render_template("terminology/index.html", coverage=svc.coverage(current_user.organization_id),
                           hpt_count=HptProduct.query.count(), hpt_url=bool(current_app.config.get("HPT_REGISTRY_URL")))


@terminology_bp.route("/terminology/import", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def import_csv():
    kind, f = request.form.get("kind"), request.files.get("file")
    if kind not in svc.TARGETS or not f:
        return jsonify(success=False, error="Choose what to import and a CSV file."), 400
    try:
        text = f.read(3_000_000).decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(success=False, error="The file must be UTF-8 text."), 400
    n, errors = svc.import_mapping_csv(kind, text, current_user.organization_id)
    log_action(current_user, "import", "Terminology", None, {"kind": kind, "updated": n, "errors": len(errors)})
    db.session.commit()
    return jsonify(success=n > 0, updated=n, errors=errors[:20])


@terminology_bp.route("/terminology/hpt/search")
@login_required
def hpt_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[])
    try:
        rows = svc.hpt_search(current_app.config, q)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        return jsonify(results=[], error=str(e)[:150]), 502
    return jsonify(results=[{"hpt_code": r.hpt_code, "name": r.name, "generic_name": r.generic_name, "form": r.form,
                             "strength": r.strength} for r in rows])
