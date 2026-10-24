"""System diagnostics & maintenance — platform-wide, for System Maintainer
accounts only (Role.scope == "platform"). See seed.py for how that role is
defined and app/admin/routes.py:users_create() for why it can't be granted
from the ordinary user-management screen.

Every check here is read-only until its matching /fix/<key> route is hit,
and every fix is a narrow, well-defined data-consistency repair — not a
general "run arbitrary code" mechanism. That's a deliberate limit: this
page can find and correct the specific classes of drift it knows how to
check for, the same way a real ops runbook would, but it can't diagnose or
patch a bug it has no check written for. New checks get added here as new
failure modes turn up in practice.
"""
import datetime
import platform
import secrets
import shutil
import sys

import flask
import sqlalchemy
from flask import Blueprint, render_template, jsonify, current_app, request
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.models import (
    Bed, Admission, StockBatch, StockTransaction, User, Patient, Hospital, Organization,
    AuditLog, Role, SubscriptionPriceRule, HOSPITAL_LEVELS, PLAN_TYPES, log_action,
)
from app.subscription.pricing import resolve_price

sysadmin_bp = Blueprint("sysadmin", __name__, template_folder="../templates/sysadmin")


# ---------------------------------------------------------------------------
# Diagnostic checks. Each is (key, label, run() -> list[dict], fix() or None)
# run() returns one dict per problem row: at least {"id", "description"}.
# fix() takes no args, repairs every currently-detected instance, and
# returns how many it fixed. Keep each check narrow and reversible-in-
# spirit — these run against production data with no undo button.
# ---------------------------------------------------------------------------

def _check_occupied_beds_without_admission():
    occupied = Bed.query.filter_by(status="Occupied").all()
    active_bed_ids = {a.bed_id for a in Admission.query.filter_by(status="Active").all() if a.bed_id}
    issues = [b for b in occupied if b.id not in active_bed_ids]
    return [{"id": b.id, "description": f"{b.ward.hospital.name} / {b.ward.name} / {b.label} marked Occupied but no active admission holds it"} for b in issues]


def _fix_occupied_beds_without_admission():
    occupied = Bed.query.filter_by(status="Occupied").all()
    active_bed_ids = {a.bed_id for a in Admission.query.filter_by(status="Active").all() if a.bed_id}
    count = 0
    for b in occupied:
        if b.id not in active_bed_ids:
            b.status = "Available"
            count += 1
    db.session.commit()
    return count


def _check_active_admissions_bed_not_occupied():
    admissions = Admission.query.filter(Admission.status == "Active", Admission.bed_id.isnot(None)).all()
    issues = [a for a in admissions if a.bed and a.bed.status != "Occupied"]
    return [{"id": a.id, "description": f"{a.patient.full_name} — active admission but bed {a.bed.label} shows '{a.bed.status}'"} for a in issues]


def _fix_active_admissions_bed_not_occupied():
    admissions = Admission.query.filter(Admission.status == "Active", Admission.bed_id.isnot(None)).all()
    count = 0
    for a in admissions:
        if a.bed and a.bed.status != "Occupied":
            a.bed.status = "Occupied"
            count += 1
    db.session.commit()
    return count


def _check_active_admissions_on_closed_visits():
    admissions = Admission.query.filter_by(status="Active").all()
    issues = [a for a in admissions if a.visit and a.visit.status in ("Completed", "Discharged")]
    return [{"id": a.id, "description": f"{a.patient.full_name} — admission still Active but visit is {a.visit.status}"} for a in issues]


def _fix_active_admissions_on_closed_visits():
    admissions = Admission.query.filter_by(status="Active").all()
    count = 0
    for a in admissions:
        if a.visit and a.visit.status in ("Completed", "Discharged"):
            a.status = "Discharged"
            a.actual_discharge_date = a.actual_discharge_date or datetime.datetime.utcnow()
            if a.bed:
                a.bed.status = "Available"
            count += 1
    db.session.commit()
    return count


def _check_negative_stock():
    batches = StockBatch.query.filter(StockBatch.quantity_remaining < 0).all()
    return [{"id": b.id, "description": f"{b.hospital.name} — {b.drug.name} batch {b.batch_number or b.id}: {b.quantity_remaining} remaining"} for b in batches]


def _fix_negative_stock():
    batches = StockBatch.query.filter(StockBatch.quantity_remaining < 0).all()
    count = 0
    for b in batches:
        correction = -b.quantity_remaining  # bring it back up to zero
        db.session.add(StockTransaction(
            hospital_id=b.hospital_id, stock_batch_id=b.id, transaction_type="Correction",
            quantity_delta=correction, quantity_after=0, performed_by_id=current_user.id,
            reason="System diagnostics: corrected negative stock to zero",
        ))
        b.quantity_remaining = 0
        count += 1
    db.session.commit()
    return count


def _check_expired_stock_with_remaining():
    today = datetime.date.today()
    batches = StockBatch.query.filter(
        StockBatch.expiry_date.isnot(None), StockBatch.expiry_date < today, StockBatch.quantity_remaining > 0
    ).all()
    return [{"id": b.id, "description": f"{b.hospital.name} — {b.drug.name} batch {b.batch_number or b.id}: expired {b.expiry_date}, {b.quantity_remaining} units still on hand"} for b in batches]


def _fix_expired_stock_with_remaining():
    today = datetime.date.today()
    batches = StockBatch.query.filter(
        StockBatch.expiry_date.isnot(None), StockBatch.expiry_date < today, StockBatch.quantity_remaining > 0
    ).all()
    count = 0
    for b in batches:
        db.session.add(StockTransaction(
            hospital_id=b.hospital_id, stock_batch_id=b.id, transaction_type="Write-off (expired)",
            quantity_delta=-b.quantity_remaining, quantity_after=0, performed_by_id=current_user.id,
            reason=f"System diagnostics: expired {b.expiry_date}",
        ))
        b.quantity_remaining = 0
        count += 1
    db.session.commit()
    return count


def _check_locked_out_staff():
    users = User.query.filter_by(is_active=True, hospital_id=None).all()
    issues = [u for u in users if u.role and u.role.scope not in ("organization", "platform")]
    return [{"id": u.id, "description": f"{u.full_name} ({u.role.name}) has no hospital assigned — can't see any patients until one is set"} for u in issues]


def _check_duplicate_patients():
    from sqlalchemy import func
    dupes = (
        db.session.query(Patient.hospital_id, Patient.phone, func.count(Patient.id).label("n"))
        .filter(Patient.phone.isnot(None), Patient.phone != "")
        .group_by(Patient.hospital_id, Patient.phone)
        .having(func.count(Patient.id) > 1)
        .all()
    )
    issues = []
    for hospital_id, phone, n in dupes:
        hospital = Hospital.query.get(hospital_id)
        issues.append({"id": f"{hospital_id}-{phone}", "description": f"{hospital.name if hospital else '?'} — {n} patient records share phone {phone}"})
    return issues


DIAGNOSTIC_CHECKS = [
    {
        "key": "occupied_beds_without_admission", "label": "Beds marked Occupied with no active admission",
        "run": _check_occupied_beds_without_admission, "fix": _fix_occupied_beds_without_admission,
    },
    {
        "key": "active_admissions_bed_not_occupied", "label": "Active admissions whose bed isn't marked Occupied",
        "run": _check_active_admissions_bed_not_occupied, "fix": _fix_active_admissions_bed_not_occupied,
    },
    {
        "key": "active_admissions_on_closed_visits", "label": "Admissions still Active on a Completed/Discharged visit",
        "run": _check_active_admissions_on_closed_visits, "fix": _fix_active_admissions_on_closed_visits,
    },
    {
        "key": "negative_stock", "label": "Stock batches with negative quantity remaining",
        "run": _check_negative_stock, "fix": _fix_negative_stock,
    },
    {
        "key": "expired_stock_with_remaining", "label": "Expired stock batches still showing units on hand",
        "run": _check_expired_stock_with_remaining, "fix": _fix_expired_stock_with_remaining,
    },
    {
        "key": "locked_out_staff", "label": "Active staff with no hospital assigned (locked out)",
        "run": _check_locked_out_staff, "fix": None,
    },
    {
        "key": "duplicate_patients", "label": "Possible duplicate patient records (same phone, same hospital)",
        "run": _check_duplicate_patients, "fix": None,
    },
]


def _system_info():
    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    disk = None
    if db_uri.startswith("sqlite:///"):
        import os
        db_path = db_uri.replace("sqlite:///", "", 1)
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            usage = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)) or ".")
            disk = {
                "db_size_mb": round(size_mb, 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "total_gb": round(usage.total / (1024 ** 3), 2),
            }
    return {
        "python_version": sys.version.split()[0],
        "flask_version": flask.__version__,
        "sqlalchemy_version": sqlalchemy.__version__,
        "platform": platform.platform(),
        "db_dialect": db.engine.dialect.name,
        "disk": disk,
        "counts": {
            "organizations": Organization.query.count(),
            "hospitals": Hospital.query.count(),
            "users": User.query.count(),
            "patients": Patient.query.count(),
        },
    }


@sysadmin_bp.route("/system-maintenance", methods=["GET"])
@login_required
@permission_required("system.maintain")
def dashboard():
    results = []
    for check in DIAGNOSTIC_CHECKS:
        try:
            issues = check["run"]()
        except Exception as e:  # a broken check shouldn't take the whole page down
            issues = None
            error = str(e)
        else:
            error = None
        results.append({
            "key": check["key"], "label": check["label"],
            "issues": issues, "error": error, "fixable": check["fix"] is not None,
        })

    recent_activity = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(30).all()
    return render_template(
        "sysadmin/dashboard.html", results=results, info=_system_info(), recent_activity=recent_activity,
    )


@sysadmin_bp.route("/system-maintenance/fix/<key>", methods=["POST"])
@login_required
@permission_required("system.maintain")
def run_fix(key):
    check = next((c for c in DIAGNOSTIC_CHECKS if c["key"] == key), None)
    if not check or not check["fix"]:
        return jsonify(success=False, error="No automatic fix for this check."), 400
    try:
        fixed = check["fix"]()
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e)), 500
    log_action(current_user, "update", "SystemMaintenance", None, {"check": key, "fixed": fixed})
    db.session.commit()
    return jsonify(success=True, fixed=fixed)


# ---------------------------------------------------------------------------
# Subscription pricing — the platform owner (System Maintainer) sets what
# facilities pay, instead of prices being fixed in code. A rule can target
# one hospital or all hospitals, and one level or all levels, for the monthly
# subscription and/or the one-time purchase. See app/subscription/pricing.py
# for how rules are matched against the default price tables.
# ---------------------------------------------------------------------------

MAX_PRICE = 10_000_000


def _parse_rule_fields(data, creating):
    """-> (fields dict, error string). Validates everything server-side."""
    out = {}
    if creating:
        plan_type = (data.get("plan_type") or "subscription").strip()
        if plan_type not in PLAN_TYPES:
            return None, "Invalid plan type."
        out["plan_type"] = plan_type

        scope = (data.get("scope") or "all").strip()
        if scope == "hospital":
            try:
                hospital = Hospital.query.get(int(data.get("hospital_id")))
            except (TypeError, ValueError):
                hospital = None
            if not hospital:
                return None, "Choose the hospital this price is for."
            out["hospital_id"] = hospital.id
            out["level"] = None  # a named hospital doesn't need a level filter
        elif scope == "all":
            level = (data.get("level") or "").strip()
            if level and level not in HOSPITAL_LEVELS:
                return None, "Invalid level."
            out["hospital_id"] = None
            out["level"] = level or None
        else:
            return None, "Invalid scope."

    try:
        from decimal import Decimal, InvalidOperation
        amount = Decimal(str(data.get("amount", "")).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None, "Enter the price as a number in KES."
    if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
        return None, f"Price must be between 0 and {MAX_PRICE:,} KES."
    out["amount"] = amount.quantize(Decimal("0.01"))
    out["note"] = ((data.get("note") or "").strip()[:300]) or None
    return out, None


def _rule_summary(rule):
    return {"scope": rule.scope_label, "plan_type": rule.plan_type, "amount": float(rule.amount), "active": rule.is_active}


@sysadmin_bp.route("/system-maintenance/pricing", methods=["GET"])
@login_required
@permission_required("system.maintain")
def pricing():
    rules = SubscriptionPriceRule.query.order_by(
        SubscriptionPriceRule.is_active.desc(), SubscriptionPriceRule.updated_at.desc()
    ).all()
    hospitals = Hospital.query.order_by(Hospital.name).all()
    # What each customer organization is charged right now, and why.
    effective = []
    for org in Organization.query.order_by(Organization.name).all():
        if any(u.role and u.role.scope == "platform" for u in org.users):
            continue  # the maintenance placeholder org isn't a paying customer
        monthly, m_rule = resolve_price(org, "subscription")
        once, p_rule = resolve_price(org, "purchase")
        effective.append({"org": org, "monthly": monthly, "m_rule": m_rule, "once": once, "p_rule": p_rule})
    return render_template(
        "sysadmin/pricing.html", rules=rules, hospitals=hospitals, levels=HOSPITAL_LEVELS,
        effective=effective,
    )


@sysadmin_bp.route("/system-maintenance/pricing", methods=["POST"])
@login_required
@permission_required("system.maintain")
def pricing_create():
    data = request.get_json(silent=True) or request.form
    fields, error = _parse_rule_fields(data, creating=True)
    if error:
        return jsonify(success=False, error=error), 400

    duplicate = SubscriptionPriceRule.query.filter_by(
        plan_type=fields["plan_type"], hospital_id=fields["hospital_id"], level=fields["level"], is_active=True,
    ).first()
    if duplicate:
        return jsonify(success=False, error="An active rule for exactly this scope already exists — edit or deactivate it instead."), 400

    rule = SubscriptionPriceRule(created_by_id=current_user.id, **fields)
    db.session.add(rule)
    db.session.flush()
    log_action(current_user, "create", "SubscriptionPriceRule", rule.id, _rule_summary(rule))
    db.session.commit()
    return jsonify(success=True, id=rule.id)


@sysadmin_bp.route("/system-maintenance/pricing/<int:rule_id>/update", methods=["POST"])
@login_required
@permission_required("system.maintain")
def pricing_update(rule_id):
    rule = SubscriptionPriceRule.query.get_or_404(rule_id)
    data = request.get_json(silent=True) or request.form
    fields, error = _parse_rule_fields(data, creating=False)
    if error:
        return jsonify(success=False, error=error), 400
    before = _rule_summary(rule)
    rule.amount = fields["amount"]
    rule.note = fields["note"]
    log_action(current_user, "update", "SubscriptionPriceRule", rule.id, {"before": before, "after": _rule_summary(rule)})
    db.session.commit()
    return jsonify(success=True)


@sysadmin_bp.route("/system-maintenance/pricing/<int:rule_id>/toggle", methods=["POST"])
@login_required
@permission_required("system.maintain")
def pricing_toggle(rule_id):
    rule = SubscriptionPriceRule.query.get_or_404(rule_id)
    if not rule.is_active:
        clash = SubscriptionPriceRule.query.filter(
            SubscriptionPriceRule.id != rule.id, SubscriptionPriceRule.is_active.is_(True),
            SubscriptionPriceRule.plan_type == rule.plan_type,
            SubscriptionPriceRule.hospital_id == rule.hospital_id, SubscriptionPriceRule.level == rule.level,
        ).first()
        if clash:
            return jsonify(success=False, error="Another active rule already covers this exact scope."), 400
    rule.is_active = not rule.is_active
    log_action(current_user, "update", "SubscriptionPriceRule", rule.id, {"is_active": rule.is_active})
    db.session.commit()
    return jsonify(success=True, active=rule.is_active)


@sysadmin_bp.route("/system-maintenance/pricing/<int:rule_id>/delete", methods=["POST"])
@login_required
@permission_required("system.maintain")
def pricing_delete(rule_id):
    rule = SubscriptionPriceRule.query.get_or_404(rule_id)
    log_action(current_user, "delete", "SubscriptionPriceRule", rule.id, _rule_summary(rule))
    db.session.delete(rule)
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Password resets — System Maintainer only. Facility admins (CEO, Admin,
# Hospital Manager) can create staff accounts but cannot reset anyone's
# password afterwards; staff who are locked out contact the platform owner.
# The temporary password is generated here, shown once, never stored in
# plain text or written to the audit log, and the user must replace it at
# their next login (see the must_change_password gate in app/__init__.py).
# ---------------------------------------------------------------------------

# no 0/O/1/l/I — the password is read out or typed from a phone screen
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_temp_password(length=12):
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


@sysadmin_bp.route("/system-maintenance/users", methods=["GET"])
@login_required
@permission_required("system.maintain")
def users():
    q = (request.args.get("q") or "").strip()
    query = User.query.join(Role, User.role_id == Role.id).outerjoin(Hospital, User.hospital_id == Hospital.id)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            User.username.ilike(like), User.full_name.ilike(like), User.email.ilike(like),
            Hospital.name.ilike(like), Role.name.ilike(like),
        ))
    found = query.order_by(User.full_name).limit(50).all()
    return render_template("sysadmin/users.html", users=found, q=q)


@sysadmin_bp.route("/system-maintenance/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@permission_required("system.maintain")
def reset_password(user_id):
    target = db.session.get(User, user_id)
    if not target:
        return jsonify(success=False, error="User not found."), 404
    if target.id == current_user.id:
        return jsonify(success=False, error="Use Change Password for your own account."), 400

    temp = _new_temp_password()
    target.set_password(temp)
    target.must_change_password = True      # forced to choose their own at next request
    target.failed_login_attempts = 0
    target.locked_until = None              # a reset also clears a lockout
    log_action(current_user, "update", "User", target.id,
               {"password_reset_by_system_maintainer": True, "target": target.username})
    db.session.commit()
    return jsonify(success=True, temp_password=temp, username=target.username)
