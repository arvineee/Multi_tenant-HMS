import datetime
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import (
    Visit, Prescription, PrescriptionItem, StockBatch, StockTransaction, Drug,
    log_action, LOW_STOCK_THRESHOLD,
)

pharmacy_bp = Blueprint("pharmacy", __name__, template_folder="../templates/pharmacy")


def _visit_or_403(visit_id):
    visit = Visit.query.get_or_404(visit_id)
    if visit.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return visit


def _stock_on_hand(hospital_id, drug_id):
    today = datetime.date.today()
    batches = StockBatch.query.filter(
        StockBatch.hospital_id == hospital_id,
        StockBatch.drug_id == drug_id,
        StockBatch.quantity_remaining > 0,
    ).filter(
        db.or_(StockBatch.expiry_date.is_(None), StockBatch.expiry_date >= today)
    ).all()
    return sum(b.quantity_remaining for b in batches)


# ---------------------------------------------------------------------------
# Doctor: create a prescription (one or more drug lines) for a visit
# ---------------------------------------------------------------------------

@pharmacy_bp.route("/visits/<int:visit_id>/prescriptions", methods=["POST"])
@login_required
@permission_required("prescription.create")
def create_prescription(visit_id):
    visit = _visit_or_403(visit_id)
    data = request.get_json(silent=True) or request.form
    items = data.get("items") or []

    if not items:
        return jsonify(success=False, error="Add at least one drug."), 400

    prescription = Prescription(
        hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
        doctor_id=current_user.id,
    )
    db.session.add(prescription)
    db.session.flush()

    for item in items:
        drug_id = item.get("drug_id")
        if not drug_id:
            continue
        drug = Drug.query.get(drug_id)
        if not drug or drug.organization_id != current_user.organization_id:
            continue  # skip anything that isn't a valid drug in this organization's formulary
        quantity = item.get("quantity") or 1
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1

        db.session.add(PrescriptionItem(
            prescription_id=prescription.id,
            drug_id=drug_id,
            dosage=item.get("dosage"),
            frequency=item.get("frequency"),
            duration=item.get("duration"),
            instructions=item.get("instructions"),
            quantity_prescribed=max(quantity, 1),
            status="Pending",
        ))

    if visit.status == "Triaged":
        visit.status = "In Consultation"

    log_action(current_user, "create", "Prescription", prescription.id, {"visit_id": visit.id, "items": len(items)})
    db.session.commit()
    return jsonify(success=True, id=prescription.id)


# ---------------------------------------------------------------------------
# Pharmacist: worklist + dispensing (FEFO consumption across batches)
# ---------------------------------------------------------------------------

@pharmacy_bp.route("/pharmacy/worklist", methods=["GET"])
@login_required
@permission_required("pharmacy.dispense")
def worklist():
    hospital_ids = current_user.accessible_hospital_ids()
    items = (
        PrescriptionItem.query.join(Prescription)
        .filter(
            Prescription.hospital_id.in_(hospital_ids),
            PrescriptionItem.status.in_(["Pending", "Partially Dispensed", "Out of Stock"]),
        )
        .order_by(Prescription.created_at.asc())
        .all()
    )
    stock_levels = {
        item.id: _stock_on_hand(item.prescription.hospital_id, item.drug_id)
        for item in items
    }

    # What this patient has already been dispensed before (any prior
    # visit), so the pharmacist can see at a glance whether the prescribed
    # drug has been given before — useful context alongside the patient's
    # recorded allergies for deciding whether to substitute.
    patient_ids = {item.prescription.patient_id for item in items}
    prior_history = {}
    if patient_ids:
        prior_items = (
            PrescriptionItem.query.join(Prescription)
            .filter(Prescription.patient_id.in_(patient_ids), PrescriptionItem.quantity_dispensed > 0)
            .order_by(Prescription.created_at.desc())
            .all()
        )
        for pi in prior_items:
            prior_history.setdefault(pi.prescription.patient_id, [])
            names = {h["name"] for h in prior_history[pi.prescription.patient_id]}
            drug_name = pi.effective_drug.name
            if drug_name not in names and len(prior_history[pi.prescription.patient_id]) < 8:
                prior_history[pi.prescription.patient_id].append({
                    "name": drug_name, "date": pi.prescription.created_at,
                })

    return render_template(
        "pharmacy/worklist.html", items=items, stock_levels=stock_levels, prior_history=prior_history,
    )


def _draw_stock_fefo(hospital_id, drug_id, quantity, prescription_item_id, performed_by_id):
    """Consume `quantity` units of a drug from earliest-expiring batches
    first (FEFO), recording a StockTransaction per batch touched, and
    return the total charge in Decimal. Raises ValueError (with a
    user-facing message) if there isn't enough stock. Shared by pharmacy
    dispensing and the triage relief-medication flow so both draw down
    the same real inventory and can't double-book it."""
    today = datetime.date.today()
    batches = StockBatch.query.filter(
        StockBatch.hospital_id == hospital_id,
        StockBatch.drug_id == drug_id,
        StockBatch.quantity_remaining > 0,
    ).filter(
        db.or_(StockBatch.expiry_date.is_(None), StockBatch.expiry_date >= today)
    ).order_by(StockBatch.expiry_date.asc().nullslast()).with_for_update().all()

    available = sum(b.quantity_remaining for b in batches)
    if quantity > available:
        raise ValueError(f"Only {available} units in stock.")

    remaining_to_take = quantity
    charged_amount = Decimal("0")
    for batch in batches:
        if remaining_to_take <= 0:
            break
        take = min(batch.quantity_remaining, remaining_to_take)
        batch.quantity_remaining -= take
        charged_amount += take * (batch.selling_price or Decimal("0"))
        remaining_to_take -= take
        db.session.add(StockTransaction(
            hospital_id=hospital_id,
            stock_batch_id=batch.id,
            transaction_type="Dispense",
            quantity_delta=-take,
            quantity_after=batch.quantity_remaining,
            prescription_item_id=prescription_item_id,
            performed_by_id=performed_by_id,
        ))
    return charged_amount


@pharmacy_bp.route("/prescription-items/<int:item_id>/dispense", methods=["POST"])
@login_required
@permission_required("pharmacy.dispense")
def dispense_item(item_id):
    item = PrescriptionItem.query.get_or_404(item_id)
    hospital_id = item.prescription.hospital_id
    if hospital_id not in current_user.accessible_hospital_ids():
        abort(403)

    data = request.get_json(silent=True) or request.form
    try:
        quantity = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        quantity = 0

    if quantity <= 0:
        return jsonify(success=False, error="Enter a quantity to dispense."), 400
    if quantity > item.quantity_remaining:
        return jsonify(success=False, error=f"Only {item.quantity_remaining} remaining on this prescription."), 400

    # --- optional substitution: pharmacist gives a different drug than
    # what was prescribed (an allergy, a known side effect, the patient's
    # already tried it without effect, or it's out of stock) and/or
    # adjusts the dosage. The original prescription is never touched —
    # this only records what was actually handed over. A reason is
    # required whenever the drug itself is swapped, for the record. ---
    substitute_drug_id = data.get("substitute_drug_id")
    dispensed_dosage = (data.get("dispensed_dosage") or "").strip()
    substitution_reason = (data.get("substitution_reason") or "").strip()
    dispense_drug_id = item.drug_id  # which drug's stock we actually draw from

    if substitute_drug_id and str(substitute_drug_id) != str(item.drug_id):
        substitute_drug = Drug.query.get(substitute_drug_id)
        if not substitute_drug or substitute_drug.organization_id != current_user.organization_id:
            return jsonify(success=False, error="Invalid substitute drug."), 400
        if not substitution_reason:
            return jsonify(success=False, error="Give a reason for substituting the drug (allergy, side effect, prior use, out of stock, etc.)."), 400
        item.dispensed_drug_id = substitute_drug.id
        item.substitution_reason = substitution_reason
        dispense_drug_id = substitute_drug.id

    if dispensed_dosage and dispensed_dosage != (item.dosage or ""):
        item.dispensed_dosage = dispensed_dosage
        if not item.substitution_reason and substitution_reason:
            item.substitution_reason = substitution_reason

    try:
        charged_amount = _draw_stock_fefo(hospital_id, dispense_drug_id, quantity, item.id, current_user.id)
    except ValueError as e:
        db.session.rollback()
        return jsonify(success=False, error=f"{e} ({item.effective_drug.name})"), 400

    item.quantity_dispensed += quantity
    item.billed_amount = (item.billed_amount or 0) + charged_amount
    item.status = "Dispensed" if item.quantity_remaining == 0 else "Partially Dispensed"

    from app.billing.routes import sync_admission_insurance_flag
    sync_admission_insurance_flag(item.prescription.visit)

    log_action(current_user, "update", "PrescriptionItem", item.id, {
        "dispensed": quantity,
        "substituted": item.is_substituted,
        "dosage_adjusted": item.is_dosage_adjusted,
    })
    db.session.commit()
    return jsonify(
        success=True, status=item.status, remaining=item.quantity_remaining,
        insurance_limit_reached=bool(item.prescription.visit.admission and item.prescription.visit.admission.insurance_limit_reached),
    )


@pharmacy_bp.route("/prescription-items/<int:item_id>/cancel", methods=["POST"])
@login_required
@permission_required("pharmacy.dispense")
def cancel_item(item_id):
    item = PrescriptionItem.query.get_or_404(item_id)
    if item.prescription.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    item.status = "Cancelled"
    log_action(current_user, "update", "PrescriptionItem", item.id, {"cancelled": True})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Stock — receiving new batches and viewing levels
# ---------------------------------------------------------------------------

@pharmacy_bp.route("/prescriptions/<int:prescription_id>/print", methods=["GET"])
@login_required
@permission_required("patient.view")
def print_prescription(prescription_id):
    prescription = Prescription.query.get_or_404(prescription_id)
    if prescription.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    hospital = prescription.visit.hospital
    return render_template("pharmacy/prescription_print.html", prescription=prescription, hospital=hospital)


@pharmacy_bp.route("/pharmacy/stock", methods=["GET"])
@login_required
@permission_required("pharmacy.stock")
def stock_overview():
    if not current_user.hospital_id:
        return render_template(
            "pharmacy/stock.html", drug_levels=[], batches=[],
            low_stock_threshold=LOW_STOCK_THRESHOLD, today=datetime.date.today(),
        )

    threshold = current_user.hospital.low_stock_threshold
    drugs = Drug.query.filter_by(organization_id=current_user.organization_id, is_active=True).order_by(Drug.name).all()
    drug_levels = [
        {"drug": d, "on_hand": _stock_on_hand(current_user.hospital_id, d.id)}
        for d in drugs
    ]
    batches = StockBatch.query.filter_by(hospital_id=current_user.hospital_id).order_by(
        StockBatch.expiry_date.asc().nullslast()
    ).all()
    return render_template(
        "pharmacy/stock.html", drug_levels=drug_levels, batches=batches,
        low_stock_threshold=threshold, today=datetime.date.today(),
    )


@pharmacy_bp.route("/pharmacy/stock", methods=["POST"])
@login_required
@permission_required("pharmacy.stock")
def receive_stock():
    if not current_user.hospital_id:
        return jsonify(success=False, error="Your account isn't tied to a hospital."), 400

    data = request.get_json(silent=True) or request.form
    drug_id = data.get("drug_id")
    quantity = data.get("quantity")
    if not drug_id or not quantity:
        return jsonify(success=False, error="Drug and quantity are required."), 400

    drug = Drug.query.get(drug_id)
    if not drug or drug.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Invalid drug."), 400
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Quantity must be a number."), 400
    if quantity <= 0:
        return jsonify(success=False, error="Quantity must be greater than zero."), 400

    expiry_date = None
    if data.get("expiry_date"):
        try:
            expiry_date = datetime.datetime.strptime(data["expiry_date"], "%Y-%m-%d").date()
        except ValueError:
            expiry_date = None

    batch = StockBatch(
        hospital_id=current_user.hospital_id, drug_id=drug_id,
        batch_number=data.get("batch_number"),
        quantity_received=quantity, quantity_remaining=quantity,
        unit_cost=data.get("unit_cost") or None,
        selling_price=data.get("selling_price") or None,
        expiry_date=expiry_date,
        received_by_id=current_user.id,
    )
    db.session.add(batch)
    db.session.flush()  # so batch.id is populated before we log it
    log_action(current_user, "create", "StockBatch", batch.id, {"drug_id": drug_id, "quantity": quantity})
    db.session.commit()
    return jsonify(success=True, id=batch.id)
