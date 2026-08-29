from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from app.models import DiagnosisCode, Drug, LabTest, RadiologyTest
from app.level_policy import hospital_allowed_radiology_modalities

api_bp = Blueprint("api", __name__)


@api_bp.route("/api/diagnosis-search", methods=["GET"])
@login_required
def diagnosis_search():
    if not current_user.has_permission("consultation.create"):
        return jsonify(results=[]), 403

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[])

    like = f"%{q}%"
    matches = DiagnosisCode.query.filter(
        DiagnosisCode.is_active.is_(True)
    ).filter(
        (DiagnosisCode.code.ilike(like)) | (DiagnosisCode.description.ilike(like))
    ).order_by(DiagnosisCode.code).limit(20).all()

    return jsonify(results=[{"id": d.id, "code": d.code, "description": d.description} for d in matches])


@api_bp.route("/api/drug-search", methods=["GET"])
@login_required
def drug_search():
    if not current_user.has_permission("prescription.create"):
        return jsonify(results=[]), 403

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[])

    like = f"%{q}%"
    matches = Drug.query.filter(
        Drug.organization_id == current_user.organization_id,
        Drug.is_active.is_(True),
    ).filter(
        (Drug.name.ilike(like)) | (Drug.generic_name.ilike(like))
    ).order_by(Drug.name).limit(20).all()

    return jsonify(results=[
        {"id": d.id, "name": d.name, "generic_name": d.generic_name, "form": d.form, "strength": d.strength}
        for d in matches
    ])


@api_bp.route("/api/lab-test-search", methods=["GET"])
@login_required
def lab_test_search():
    if not current_user.has_permission("lab.order"):
        return jsonify(results=[]), 403

    q = (request.args.get("q") or "").strip()
    query = LabTest.query.filter(
        LabTest.organization_id == current_user.organization_id,
        LabTest.is_active.is_(True),
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            (LabTest.name.ilike(like)) | (LabTest.code.ilike(like)) | (LabTest.category.ilike(like))
        )
    matches = query.order_by(LabTest.name).limit(20).all()

    return jsonify(results=[
        {"id": t.id, "name": t.name, "code": t.code, "category": t.category} for t in matches
    ])


@api_bp.route("/api/radiology-test-search", methods=["GET"])
@login_required
def radiology_test_search():
    if not current_user.has_permission("radiology.order"):
        return jsonify(results=[]), 403

    allowed_modalities = hospital_allowed_radiology_modalities(current_user.hospital)
    if not allowed_modalities:
        return jsonify(results=[])

    q = (request.args.get("q") or "").strip()
    query = RadiologyTest.query.filter(
        RadiologyTest.organization_id == current_user.organization_id,
        RadiologyTest.is_active.is_(True),
        RadiologyTest.modality.in_(allowed_modalities),
    )
    if q:
        like = f"%{q}%"
        query = query.filter((RadiologyTest.name.ilike(like)) | (RadiologyTest.code.ilike(like)))
    matches = query.order_by(RadiologyTest.name).limit(20).all()

    return jsonify(results=[
        {"id": t.id, "name": t.name, "code": t.code, "modality": t.modality} for t in matches
    ])
