import datetime

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import (
    SubscriptionPayment, SUBSCRIPTION_PRICING, ONE_TIME_PRICING,
    SUBSCRIPTION_PERIOD_DAYS, PLAN_TYPES, log_action,
)
from app.subscription import intasend_client

subscription_bp = Blueprint("subscription", __name__, template_folder="../templates/subscription")


def _now():
    return datetime.datetime.utcnow()


def _activate_payment(payment):
    """Applies whatever the payment actually bought: a one-time purchase
    grants permanent access with nothing left to track; a subscription
    payment extends current_period_end from whichever is later — right
    now, or the current paid-through date — so paying early stacks on
    top of remaining time instead of wasting it."""
    org = payment.organization
    if payment.plan_type == "purchase":
        org.subscription_status = "purchased"
        org.current_period_end = None
    else:
        base = org.current_period_end if org.current_period_end and org.current_period_end > _now() else _now()
        org.current_period_end = base + datetime.timedelta(days=SUBSCRIPTION_PERIOD_DAYS)
        org.subscription_status = "active"


def _can_manage_billing(user):
    return user.has_permission("settings.edit") or user.role.scope == "organization"


@subscription_bp.route("/subscription", methods=["GET"])
@login_required
def status():
    org = current_user.organization
    monthly_price = SUBSCRIPTION_PRICING.get(org.plan_level, 0)
    purchase_price = ONE_TIME_PRICING.get(org.plan_level, 0)
    recent_payments = SubscriptionPayment.query.filter_by(organization_id=org.id).order_by(
        SubscriptionPayment.created_at.desc()
    ).limit(5).all()
    return render_template(
        "subscription/status.html", org=org, monthly_price=monthly_price, purchase_price=purchase_price,
        recent_payments=recent_payments, can_pay=_can_manage_billing(current_user),
        intasend_configured=intasend_client.is_configured(),
    )


@subscription_bp.route("/subscription/checkout", methods=["POST"])
@login_required
def checkout():
    org = current_user.organization
    if not _can_manage_billing(current_user):
        return jsonify(success=False, error="Only an organization owner or manager can manage billing."), 403

    data = request.get_json(silent=True) or request.form
    phone_number = (data.get("phone_number") or "").strip()
    plan_type = data.get("plan_type", "subscription")
    if plan_type not in PLAN_TYPES:
        return jsonify(success=False, error="Invalid plan type."), 400
    if not phone_number:
        return jsonify(success=False, error="Enter the M-Pesa phone number to charge."), 400

    price_table = ONE_TIME_PRICING if plan_type == "purchase" else SUBSCRIPTION_PRICING
    price = price_table.get(org.plan_level, 0)

    if price <= 0:
        payment = SubscriptionPayment(
            organization_id=org.id, plan_type=plan_type, amount=0, phone_number=phone_number,
            status="Completed", provider="FREE",
        )
        db.session.add(payment)
        _activate_payment(payment)
        db.session.commit()
        return jsonify(success=True, free=True, message="This plan level is free — your account is active.")

    api_ref = f"{plan_type}-{org.id}-{int(_now().timestamp())}"
    payment = SubscriptionPayment(
        organization_id=org.id, plan_type=plan_type, amount=price, phone_number=phone_number,
        status="Pending", api_ref=api_ref, provider="M-PESA",
    )
    db.session.add(payment)
    db.session.flush()

    label = "outright purchase" if plan_type == "purchase" else "subscription"
    success, invoice_id, error = intasend_client.initiate_stk_push(
        phone_number=phone_number, email=current_user.email, amount=price,
        narrative=f"MediCore HMIS {label} — {org.plan_level}", api_ref=api_ref,
    )

    if not success:
        payment.status = "Failed"
        payment.failure_reason = error
        db.session.commit()
        return jsonify(success=False, error=error, payment_id=payment.id,
                        simulate_available=not intasend_client.is_configured())

    payment.invoice_id = invoice_id
    db.session.commit()
    return jsonify(success=True, payment_id=payment.id, invoice_id=invoice_id)


@subscription_bp.route("/subscription/poll/<int:payment_id>", methods=["GET"])
@login_required
def poll_payment(payment_id):
    payment = SubscriptionPayment.query.get_or_404(payment_id)
    if payment.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Not allowed."), 403

    if payment.status == "Pending" and payment.invoice_id:
        state = intasend_client.check_status(payment.invoice_id)
        if state == "COMPLETE":
            payment.status = "Completed"
            _activate_payment(payment)
            db.session.commit()
        elif state == "FAILED":
            payment.status = "Failed"
            payment.failure_reason = "Payment failed or was cancelled."
            db.session.commit()

    return jsonify(success=True, status=payment.status,
                    subscription_status=payment.organization.subscription_status)


@subscription_bp.route("/subscription/simulate/<int:payment_id>", methods=["POST"])
@login_required
def simulate_payment(payment_id):
    """Dev-only: lets you test the full trial -> pay -> reactivate flow
    (for either plan type) without live IntaSend credentials. Disabled
    the moment real credentials are configured."""
    if intasend_client.is_configured():
        return jsonify(success=False, error="Simulation is disabled once real payment credentials are configured."), 403

    payment = SubscriptionPayment.query.get_or_404(payment_id)
    if payment.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Not allowed."), 403

    payment.status = "Completed"
    _activate_payment(payment)
    log_action(current_user, "update", "SubscriptionPayment", payment.id, {"simulated": True, "plan_type": payment.plan_type})
    db.session.commit()
    return jsonify(success=True)


@subscription_bp.route("/subscription/webhook", methods=["POST"])
@csrf.exempt  # IntaSend calls this directly — no browser session/CSRF token to check
def webhook():
    payload = request.get_json(silent=True) or {}

    if not intasend_client.verify_webhook_challenge(payload.get("challenge")):
        return jsonify(error="Invalid challenge"), 401

    invoice_id = payload.get("invoice_id")
    state = payload.get("state")
    if not invoice_id:
        return jsonify(error="Missing invoice_id"), 400

    payment = SubscriptionPayment.query.filter_by(invoice_id=invoice_id).first()
    if not payment:
        return jsonify(error="Unknown invoice"), 404

    if state == "COMPLETE" and payment.status != "Completed":
        payment.status = "Completed"
        _activate_payment(payment)
        db.session.commit()
    elif state == "FAILED":
        payment.status = "Failed"
        payment.failure_reason = "Payment failed per IntaSend webhook."
        db.session.commit()

    return jsonify(success=True)
