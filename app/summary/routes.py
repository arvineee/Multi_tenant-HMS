from flask import Blueprint, render_template, jsonify, Response
from flask_login import login_required, current_user

import json

from app.extensions import db
from app.decorators import permission_required
from app.models import log_action
from app.security.access import load_patient
from app.summary.service import gather_summary
from app.hie import fhir
from flask import current_app

summary_bp = Blueprint("summary", __name__, template_folder="../templates/summary")


@summary_bp.route("/patients/<int:patient_id>/summary")
@login_required
@permission_required("patient.view")
def view(patient_id):
    patient = load_patient(patient_id, section="clinical-summary")
    return render_template("summary/summary.html", s=gather_summary(patient), patient=patient,
                           can_hie=current_user.has_permission("hie.manage"))


@summary_bp.route("/patients/<int:patient_id>/summary.fhir.json")
@login_required
@permission_required("patient.view")
def fhir_json(patient_id):
    patient = load_patient(patient_id, section="clinical-summary-fhir")
    bundle = fhir.build_document_bundle(gather_summary(patient), current_app.config["HIE_IDENTIFIER_BASE"])
    log_action(current_user, "export", "Patient", patient.id, {"format": "fhir"}, patient_id=patient.id)
    db.session.commit()
    return Response(json.dumps(bundle, indent=2), mimetype="application/fhir+json",
                    headers={"Content-Disposition": f"attachment; filename=summary-{patient.patient_number}.json"})
