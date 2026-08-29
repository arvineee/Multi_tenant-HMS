import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import (
    Visit, Bill, BillLineItem, Payment, Hospital, log_action, PAYMENT_METHODS,
)

billing_bp = Blueprint("billing", __name__, template_folder="../templates/billing")


def _visit_or_403(visit_id):
    visit = Visit.query.get_or_404(visit_id)
    if visit.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return visit


def _bill_or_403(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    if bill.hospital_id not in current_user.accessible_hospital_ids():
        abort(403)
    return bill


def _build_line_items(bill, visit):
    hospital = Hospital.query.get(visit.hospital_id)
    lines = []

    # --- consultation fee ---
    if visit.consultation:
        # Kept in Decimal, same as every other line item's amount — this
        # was previously built with float(), which raises a TypeError the
        # moment it's summed alongside any Decimal-based line (pharmacy,
        # lab, radiology, admission, procedure) in Bill.total_amount.
        fee = Decimal("0")
        try:
            fee = Decimal(str(hospital.get_setting("default_consultation_fee", 0) or 0))
        except (TypeError, ValueError, InvalidOperation):
            fee = Decimal("0")
        if fee > 0:
            lines.append(BillLineItem(
                bill_id=bill.id, category="Consultation", description="Consultation fee",
                quantity=1, unit_price=fee, amount=fee,
                source_type="consultation", source_id=visit.consultation.id,
            ))

    # --- pharmacy: only what's actually been dispensed ---
    for prescription in visit.prescriptions:
        for item in prescription.items:
            if item.quantity_dispensed > 0 and item.billed_amount:
                unit_price = round(item.billed_amount / item.quantity_dispensed, 2)
                lines.append(BillLineItem(
                    bill_id=bill.id, category="Pharmacy",
                    description=f"{item.drug.name} x{item.quantity_dispensed}",
                    quantity=item.quantity_dispensed, unit_price=unit_price,
                    amount=round(item.billed_amount, 2),
                    source_type="prescription_item", source_id=item.id,
                ))

    # --- lab: only resulted tests ---
    for order in visit.lab_orders:
        if order.status == "Result Ready":
            price = order.lab_test.price or 0
            lines.append(BillLineItem(
                bill_id=bill.id, category="Lab", description=order.lab_test.name,
                quantity=1, unit_price=price, amount=price,
                source_type="lab_order", source_id=order.id,
            ))

    # --- radiology: only reported studies ---
    for order in visit.radiology_orders:
        if order.status == "Report Ready":
            price = order.radiology_test.price or 0
            lines.append(BillLineItem(
                bill_id=bill.id, category="Radiology", description=order.radiology_test.name,
                quantity=1, unit_price=price, amount=price,
                source_type="radiology_order", source_id=order.id,
            ))

    # --- inpatient bed charge ---
    if visit.admission:
        admission = visit.admission
        end = admission.actual_discharge_date.date() if admission.actual_discharge_date else datetime.date.today()
        start = admission.admission_date.date()
        days = max((end - start).days, 1)
        rate = admission.ward.daily_rate or 0
        if rate > 0:
            lines.append(BillLineItem(
                bill_id=bill.id, category="Admission",
                description=f"{admission.ward.name} bed charge x{days} day(s)",
                quantity=days, unit_price=rate, amount=days * rate,
                source_type="admission", source_id=admission.id,
            ))

        # --- ward procedures/services: only what's actually been performed ---
        for po in admission.procedure_orders:
            if po.status == "Done":
                lines.append(BillLineItem(
                    bill_id=bill.id, category="Procedure",
                    description=po.name, quantity=po.quantity, unit_price=po.unit_price,
                    amount=po.amount,
                    source_type="procedure_order", source_id=po.id,
                ))

    return lines


def sync_admission_insurance_flag(visit):
    """Call this right after anything billable happens on a visit (a lab
    result comes in, a drug is dispensed, a radiology report is filed).
    For an active inpatient stay on an insurance scheme, it recalculates
    the running total and flips Admission.insurance_limit_reached once
    the authorized amount is hit — that's the 'accumulate and trigger
    when the insurance is reached' behavior. Cash patients and anyone
    without an active admission are unaffected; nothing to enforce."""
    admission = getattr(visit, "admission", None)
    if not admission or admission.status != "Active":
        return
    admission.refresh_insurance_limit_flag()


@billing_bp.route("/visits/<int:visit_id>/generate-bill", methods=["POST"])
@login_required
@permission_required("billing.manage")
def generate_bill(visit_id):
    visit = _visit_or_403(visit_id)

    bill = visit.bill
    if bill and bill.payments:
        return jsonify(success=True, id=bill.id, refreshed=False,
                        note="Bill already has payments recorded — new charges won't auto-add. Use Add Line Item if needed.")

    if not bill:
        bill = Bill(
            hospital_id=visit.hospital_id, visit_id=visit.id, patient_id=visit.patient_id,
            insurance_scheme_id=visit.patient.insurance_scheme_id,
            created_by_id=current_user.id,
        )
        db.session.add(bill)
        db.session.flush()
    else:
        BillLineItem.query.filter_by(bill_id=bill.id).delete()
        db.session.expire(bill, ["line_items"])

    lines = _build_line_items(bill, visit)
    db.session.add_all(lines)
    db.session.flush()
    bill.refresh_status()
    log_action(current_user, "create", "Bill", bill.id, {"visit_id": visit.id, "total": bill.total_amount})

    db.session.commit()
    return jsonify(success=True, id=bill.id, total=bill.total_amount)


@billing_bp.route("/bills/<int:bill_id>/receipt", methods=["GET"])
@login_required
@permission_required("billing.manage")
def bill_receipt(bill_id):
    bill = _bill_or_403(bill_id)
    return render_template("billing/receipt.html", bill=bill)


@billing_bp.route("/bills/<int:bill_id>", methods=["GET"])
@login_required
@permission_required("billing.manage")
def bill_detail(bill_id):
    bill = _bill_or_403(bill_id)
    return render_template("billing/detail.html", bill=bill, payment_methods=PAYMENT_METHODS)


@billing_bp.route("/bills/<int:bill_id>/payments", methods=["POST"])
@login_required
@permission_required("billing.manage")
def record_payment(bill_id):
    bill = _bill_or_403(bill_id)
    data = request.get_json(silent=True) or request.form

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    method = data.get("method")

    if amount <= 0:
        return jsonify(success=False, error="Enter a payment amount."), 400
    if method not in PAYMENT_METHODS:
        return jsonify(success=False, error="Select a valid payment method."), 400
    if amount > bill.balance_due + 0.01:
        return jsonify(success=False, error=f"Amount exceeds balance due (KES {bill.balance_due:.2f})."), 400

    payment = Payment(
        bill_id=bill.id, amount=amount, method=method,
        reference=data.get("reference"), received_by_id=current_user.id,
    )
    db.session.add(payment)
    bill.payments.append(payment)  # keep the in-memory relationship in sync so refresh_status() sees it
    db.session.flush()
    bill.refresh_status()
    log_action(current_user, "create", "Payment", payment.id, {"bill_id": bill.id, "amount": amount, "method": method})

    db.session.commit()
    return jsonify(success=True, status=bill.status, balance_due=bill.balance_due)


@billing_bp.route("/bills/<int:bill_id>/waive", methods=["POST"])
@login_required
@permission_required("billing.manage")
def waive_bill(bill_id):
    bill = _bill_or_403(bill_id)
    data = request.get_json(silent=True) or request.form
    bill.status = "Waived"
    bill.notes = data.get("notes", bill.notes)
    log_action(current_user, "update", "Bill", bill.id, {"waived": True})
    db.session.commit()
    return jsonify(success=True)


@billing_bp.route("/bills/<int:bill_id>/claim-number", methods=["POST"])
@login_required
@permission_required("billing.manage")
def update_claim_number(bill_id):
    bill = _bill_or_403(bill_id)
    data = request.get_json(silent=True) or request.form
    bill.insurance_claim_number = data.get("insurance_claim_number")
    db.session.commit()
    return jsonify(success=True)


@billing_bp.route("/billing/worklist", methods=["GET"])
@login_required
@permission_required("billing.manage")
def worklist():
    hospital_ids = current_user.accessible_hospital_ids()
    bills = Bill.query.filter(
        Bill.hospital_id.in_(hospital_ids),
        Bill.status.in_(["Pending", "Partially Paid"]),
    ).order_by(Bill.created_at.asc()).all()

    # visits that are done clinically but nobody has generated a bill for yet —
    # without this, a finished visit can quietly sit un-billed with no one aware
    unbilled_visits = Visit.query.filter(
        Visit.hospital_id.in_(hospital_ids),
        Visit.status.in_(["Completed", "Discharged"]),
        ~Visit.id.in_(db.session.query(Bill.visit_id)),
    ).order_by(Visit.created_at.asc()).all()

    return render_template("billing/worklist.html", bills=bills, unbilled_visits=unbilled_visits)
