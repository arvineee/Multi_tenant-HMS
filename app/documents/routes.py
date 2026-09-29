import datetime

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import (
    Visit, MedicalDocument, DOCUMENT_TYPES, DOCUMENT_TYPE_PREFIXES, log_action,
)

documents_bp = Blueprint("documents", __name__, template_folder="../templates/documents")


def _visit_or_403(visit_id):
    visit = Visit.query.get_or_404(visit_id)
    if visit.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return visit


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _next_document_number(hospital, document_type):
    prefix = DOCUMENT_TYPE_PREFIXES.get(document_type, "DOC")
    year = datetime.date.today().year
    count = MedicalDocument.query.filter_by(hospital_id=hospital.id, document_type=document_type).filter(
        MedicalDocument.document_number.like(f"{prefix}-{hospital.code}-{year}-%")
    ).count()
    return f"{prefix}-{hospital.code}-{year}-{count + 1:04d}"


@documents_bp.route("/visits/<int:visit_id>/documents", methods=["POST"])
@login_required
@permission_required("consultation.create")
def issue_document(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form

    document_type = data.get("document_type")
    if document_type not in DOCUMENT_TYPES:
        return jsonify(success=False, error="Select a valid document type."), 400

    date_from = _parse_date(data.get("date_from"))
    date_to = _parse_date(data.get("date_to"))
    days_count = None
    if date_from and date_to:
        days_count = (date_to - date_from).days + 1
        if days_count < 1:
            return jsonify(success=False, error="End date must be on or after the start date."), 400

    doc = MedicalDocument(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
        document_type=document_type,
        document_number=_next_document_number(visit.hospital, document_type),
        diagnosis_code_id=data.get("diagnosis_code_id") or None,
        body_text=data.get("body_text"),
        recommendation=data.get("recommendation"),
        date_from=date_from, date_to=date_to, days_count=days_count,
        referred_to_facility=data.get("referred_to_facility"),
        referred_to_doctor=data.get("referred_to_doctor"),
        follow_up_date=_parse_date(data.get("follow_up_date")),
        issued_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()  # so doc.id is populated before we log it
    log_action(current_user, "create", "MedicalDocument", doc.id, {"type": document_type, "visit_id": visit.id})

    db.session.commit()
    return jsonify(success=True, id=doc.id)


@documents_bp.route("/documents/<int:doc_id>/print", methods=["GET"])
@login_required
@permission_required("patient.view")
def print_document(doc_id):
    doc = MedicalDocument.query.get_or_404(doc_id)
    if doc.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return render_template("documents/print.html", doc=doc)
