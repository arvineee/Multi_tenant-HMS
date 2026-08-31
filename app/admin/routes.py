import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import permission_required
from app.level_policy import hospital_allows_inpatient
from app.models import (
    Hospital, HOSPITAL_LEVELS, Organization, Role, User, UserHospitalAccess,
    DiagnosisCode, Drug, RadiologyTest, LabTest, InsuranceScheme, Ward, AuditLog, log_action,
)

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")

# Role scope hierarchy, broadest first — shared between users_list() (which
# role options to even show someone) and users_create() (the actual
# enforcement). Kept in one place so the two can't quietly drift apart.
SCOPE_RANK = {"department": 0, "hospital": 1, "organization": 2, "platform": 3}


# ---------------------------------------------------------------------------
# Hospital settings — name/logo/contact info, editable per-hospital
# ---------------------------------------------------------------------------

@admin_bp.route("/settings", methods=["GET"])
@login_required
@permission_required("settings.view")
def settings():
    hospital = current_user.hospital
    return render_template("admin/settings.html", hospital=hospital)


@admin_bp.route("/settings", methods=["POST"])
@login_required
@permission_required("settings.edit")
def settings_update():
    hospital = current_user.hospital
    if not hospital:
        return jsonify(success=False, error="Your account isn't tied to a single hospital."), 400

    data = request.get_json(silent=True) or request.form
    hospital.name = data.get("name", hospital.name)
    hospital.address = data.get("address", hospital.address)
    hospital.county = data.get("county", hospital.county)
    hospital.phone = data.get("phone", hospital.phone)
    hospital.email = data.get("email", hospital.email)

    if "receipt_footer" in data:
        hospital.set_setting("receipt_footer", data.get("receipt_footer"))

    log_action(current_user, "update", "Hospital", hospital.id, {"fields": list(data.keys())})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Pricing & System Settings — everything that moves money: consultation
# fee, currency, low-stock reorder threshold, insurance authorization
# defaults. Kept in one place, separate from the hospital contact-info
# settings above, and restricted to pricing.manage — the system owner
# (CEO role) only, not branch-level Admin/Hospital Manager.
# ---------------------------------------------------------------------------

@admin_bp.route("/system-settings", methods=["GET"])
@login_required
@permission_required("pricing.manage")
def system_settings():
    hospital = current_user.hospital
    schemes = InsuranceScheme.query.filter_by(
        organization_id=current_user.organization_id
    ).order_by(InsuranceScheme.name).all()
    return render_template("admin/system_settings.html", hospital=hospital, schemes=schemes)


@admin_bp.route("/system-settings", methods=["POST"])
@login_required
@permission_required("pricing.manage")
def system_settings_update():
    hospital = current_user.hospital
    if not hospital:
        return jsonify(success=False, error="Your account isn't tied to a single hospital."), 400

    data = request.get_json(silent=True) or request.form
    for key in ("currency", "default_consultation_fee", "low_stock_threshold"):
        if key in data:
            hospital.set_setting(key, data.get(key))

    log_action(current_user, "update", "Hospital", hospital.id, {"pricing_fields": list(data.keys())})
    db.session.commit()
    return jsonify(success=True)


@admin_bp.route("/system-settings/insurance/<int:scheme_id>/limit", methods=["POST"])
@login_required
@permission_required("pricing.manage")
def update_insurance_default_limit(scheme_id):
    scheme = InsuranceScheme.query.get_or_404(scheme_id)
    if scheme.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Not found."), 404

    data = request.get_json(silent=True) or request.form
    raw = data.get("default_credit_limit")
    if raw in (None, ""):
        scheme.default_credit_limit = None
    else:
        try:
            # Decimal, not float — this seeds Admission.insurance_authorized_amount,
            # a Numeric column, and keeping it Decimal end-to-end avoids the
            # float/Decimal mixing that broke Bill.total_amount earlier.
            scheme.default_credit_limit = Decimal(str(raw))
        except (TypeError, ValueError, InvalidOperation):
            return jsonify(success=False, error="Enter a valid amount."), 400

    log_action(current_user, "update", "InsuranceScheme", scheme.id, {"default_credit_limit": raw})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Hospital / branch management — CEO & superadmin only
# ---------------------------------------------------------------------------

@admin_bp.route("/hospitals", methods=["GET"])
@login_required
@permission_required("hospitals.manage")
def hospitals_list():
    hospitals = Hospital.query.filter_by(organization_id=current_user.organization_id).all()
    return render_template("admin/hospitals.html", hospitals=hospitals, levels=HOSPITAL_LEVELS)


@admin_bp.route("/hospitals", methods=["POST"])
@login_required
@permission_required("hospitals.manage")
def hospitals_create():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    level = data.get("level", "Level 4")

    if not name or not code:
        return jsonify(success=False, error="Name and code are required."), 400
    if Hospital.query.filter_by(organization_id=current_user.organization_id, code=code).first():
        return jsonify(success=False, error="That hospital code is already in use in your organization."), 400

    hospital = Hospital(
        organization_id=current_user.organization_id,
        name=name, code=code, level=level,
        address=data.get("address"), county=data.get("county"),
        phone=data.get("phone"), email=data.get("email"),
    )
    db.session.add(hospital)
    db.session.flush()  # so hospital.id is populated before we log it
    log_action(current_user, "create", "Hospital", hospital.id, {"name": name})
    db.session.commit()
    return jsonify(success=True, id=hospital.id)


# ---------------------------------------------------------------------------
# User management — scoped: Hospital Managers only manage their own hospital
# ---------------------------------------------------------------------------

@admin_bp.route("/users", methods=["GET"])
@login_required
@permission_required("users.manage")
def users_list():
    query = User.query.filter_by(organization_id=current_user.organization_id)
    if current_user.role.scope != "organization":
        query = query.filter(User.hospital_id.in_(current_user.accessible_hospital_ids()))
    users = query.all()
    # Only offer roles this user is actually allowed to grant — same rule
    # users_create() enforces server-side. This is just UI tidiness (a
    # Hospital Manager would otherwise see "CEO" in the dropdown and only
    # find out it's rejected after submitting); the real boundary is the
    # backend check, not this filter.
    my_rank = SCOPE_RANK.get(current_user.role.scope, 0)
    roles = [r for r in Role.query.order_by(Role.name).all() if SCOPE_RANK.get(r.scope, 0) <= my_rank]
    hospitals = Hospital.query.filter(Hospital.id.in_(current_user.accessible_hospital_ids())).all() \
        if current_user.role.scope != "organization" \
        else Hospital.query.filter_by(organization_id=current_user.organization_id).all()
    return render_template("admin/users.html", users=users, roles=roles, hospitals=hospitals)


@admin_bp.route("/users", methods=["POST"])
@login_required
@permission_required("users.manage")
def users_create():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    role_id = data.get("role_id")
    hospital_id = data.get("hospital_id") or None
    temp_password = data.get("password") or current_app.config["DEFAULT_TEMP_PASSWORD"]

    if not all([username, email, full_name, role_id]):
        return jsonify(success=False, error="Username, email, full name and role are required."), 400
    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify(success=False, error="Username or email already exists."), 400

    role = Role.query.get(role_id)
    if not role:
        return jsonify(success=False, error="Invalid role."), 400

    if temp_password and len(temp_password) < 8:
        return jsonify(success=False, error="Password must be at least 8 characters."), 400

    # A role can only be granted by someone whose own access is at least
    # as broad — otherwise a hospital-scoped Hospital Manager/Admin could
    # create a brand-new user with the org-wide CEO role (or, without
    # this, anyone with users.manage could hand out System Maintainer)
    # and escalate straight past their own intended scope. platform is
    # the broadest, then organization, then hospital, then department —
    # this one check covers every level, including the platform case
    # that used to be its own special-cased condition.
    if SCOPE_RANK.get(role.scope, 0) > SCOPE_RANK.get(current_user.role.scope, 0):
        return jsonify(success=False, error="You can't assign a role with broader access than your own."), 403

    if hospital_id:
        hospital = Hospital.query.get(hospital_id)
        # Regardless of the acting user's own scope, the hospital being
        # assigned must belong to their organization — otherwise the new
        # user's organization_id (set to the creator's org, below) and
        # hospital_id end up pointing at two different organizations,
        # and accessible_hospital_ids() would hand that new account
        # another organization's hospital data.
        if not hospital or hospital.organization_id != current_user.organization_id:
            return jsonify(success=False, error="Invalid hospital."), 400
        # a Hospital Manager/Admin can only create staff inside hospitals they can access
        if current_user.role.scope != "organization" and hospital.id not in current_user.accessible_hospital_ids():
            return jsonify(success=False, error="You can't assign users outside your hospital(s)."), 403

        # Facility Operator bundles a lot of permission into one account —
        # only sensible for the small, thinly-staffed facilities it was
        # built for. Kept out of larger facilities where separating who
        # triages from who bills from who dispenses is the point.
        if role.name == "Facility Operator" and hospital.level not in ("Level 1", "Level 2"):
            return jsonify(success=False, error="Facility Operator is only for Level 1 or Level 2 facilities."), 400
    elif role.name == "Facility Operator":
        return jsonify(success=False, error="Select a Level 1 or Level 2 hospital for a Facility Operator account."), 400

    user = User(
        organization_id=current_user.organization_id,
        hospital_id=hospital_id,
        role_id=role_id,
        username=username, email=email, full_name=full_name,
        phone=data.get("phone"), department=data.get("department"),
        must_change_password=True,
    )
    user.set_password(temp_password)
    db.session.add(user)
    db.session.flush()  # so user.id is populated before we log it
    log_action(current_user, "create", "User", user.id, {"username": username, "role": role.name})
    db.session.commit()
    return jsonify(success=True, id=user.id, temp_password=temp_password)


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@permission_required("users.manage")
def users_toggle_active(user_id):
    user = User.query.get_or_404(user_id)

    # This previously only restricted hospital-scoped actors (Hospital
    # Manager/Admin) and left the "organization" scope (CEO) branch
    # completely unchecked — meaning any CEO account could deactivate
    # *any* user system-wide just by guessing/incrementing user_id,
    # including staff at a totally different organization, or a System
    # Maintainer account. Every scope now gets an explicit boundary:
    # platform can act on anyone, organization is confined to its own
    # organization_id, everyone narrower is confined to hospitals they
    # can access.
    if current_user.role.scope == "platform":
        pass
    elif current_user.role.scope == "organization":
        if user.organization_id != current_user.organization_id:
            return jsonify(success=False, error="Not allowed."), 403
    else:
        if user.hospital_id not in current_user.accessible_hospital_ids():
            return jsonify(success=False, error="Not allowed."), 403

    if user.id == current_user.id:
        return jsonify(success=False, error="You can't deactivate your own account."), 400

    user.is_active = not user.is_active
    log_action(current_user, "update", "User", user.id, {"is_active": user.is_active})
    db.session.commit()
    return jsonify(success=True, is_active=user.is_active)


@admin_bp.route("/users/<int:user_id>/unlock", methods=["POST"])
@login_required
@permission_required("users.manage")
def users_unlock(user_id):
    """Clears a login lockout early — for when a legitimate staff member
    is locked out (mistyped password a few too many times) and shouldn't
    have to wait out the timer. Same authorization boundary as
    toggle-active above."""
    user = User.query.get_or_404(user_id)

    if current_user.role.scope == "platform":
        pass
    elif current_user.role.scope == "organization":
        if user.organization_id != current_user.organization_id:
            return jsonify(success=False, error="Not allowed."), 403
    else:
        if user.hospital_id not in current_user.accessible_hospital_ids():
            return jsonify(success=False, error="Not allowed."), 403

    user.failed_login_attempts = 0
    user.locked_until = None
    log_action(current_user, "update", "User", user.id, {"unlocked": True})
    db.session.commit()
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Master catalogs — diagnosis (ICD), drugs, radiology, insurance schemes
# ---------------------------------------------------------------------------

@admin_bp.route("/catalogs", methods=["GET"])
@login_required
@permission_required("catalogs.manage")
def catalogs():
    org_id = current_user.organization_id
    return render_template(
        "admin/catalogs.html",
        diagnoses=DiagnosisCode.query.order_by(DiagnosisCode.code).all(),
        drugs=Drug.query.filter_by(organization_id=org_id).order_by(Drug.name).all(),
        radiology=RadiologyTest.query.filter_by(organization_id=org_id).order_by(RadiologyTest.name).all(),
        lab_tests=LabTest.query.filter_by(organization_id=org_id).order_by(LabTest.name).all(),
        insurance=InsuranceScheme.query.filter_by(organization_id=org_id).order_by(InsuranceScheme.name).all(),
        wards=Ward.query.filter_by(hospital_id=current_user.hospital_id).order_by(Ward.name).all()
        if current_user.hospital_id else [],
        allows_inpatient=hospital_allows_inpatient(current_user.hospital),
    )


@admin_bp.route("/catalogs/diagnosis", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_diagnosis():
    # ICD-10 diagnosis codes are shared reference data across every
    # organization on this install — not tenant-specific, unlike the
    # other catalogs below.
    data = request.get_json(silent=True) or request.form
    code = (data.get("code") or "").strip().upper()
    description = (data.get("description") or "").strip()
    if not code or not description:
        return jsonify(success=False, error="Code and description are required."), 400
    if DiagnosisCode.query.filter_by(code=code).first():
        return jsonify(success=False, error="That code already exists."), 400
    item = DiagnosisCode(code=code, description=description, category=data.get("category"))
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "DiagnosisCode", item.id, {"code": code})
    db.session.commit()
    return jsonify(success=True, id=item.id)


@admin_bp.route("/catalogs/drug", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_drug():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(success=False, error="Drug name is required."), 400
    is_triage_relief = str(data.get("is_triage_relief", "")).lower() in ("1", "true", "on", "yes")
    item = Drug(
        organization_id=current_user.organization_id,
        name=name, generic_name=data.get("generic_name"),
        form=data.get("form"), strength=data.get("strength"), category=data.get("category"),
        is_triage_relief=is_triage_relief,
    )
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "Drug", item.id, {"name": name})
    db.session.commit()
    return jsonify(success=True, id=item.id)


@admin_bp.route("/catalogs/drug/<int:drug_id>/triage-relief", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def toggle_drug_triage_relief(drug_id):
    drug = Drug.query.get_or_404(drug_id)
    if drug.organization_id != current_user.organization_id:
        return jsonify(success=False, error="Not found."), 404
    data = request.get_json(silent=True) or request.form
    drug.is_triage_relief = str(data.get("is_triage_relief", "")).lower() in ("1", "true", "on", "yes")
    log_action(current_user, "update", "Drug", drug.id, {"is_triage_relief": drug.is_triage_relief})
    db.session.commit()
    return jsonify(success=True, is_triage_relief=drug.is_triage_relief)


@admin_bp.route("/catalogs/radiology", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_radiology():
    data = request.get_json(silent=True) or request.form
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not code or not name:
        return jsonify(success=False, error="Code and name are required."), 400
    if RadiologyTest.query.filter_by(organization_id=current_user.organization_id, code=code).first():
        return jsonify(success=False, error="That code already exists."), 400
    item = RadiologyTest(
        organization_id=current_user.organization_id,
        code=code, name=name, modality=data.get("modality"), price=data.get("price") or 0,
    )
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "RadiologyTest", item.id, {"code": code, "name": name})
    db.session.commit()
    return jsonify(success=True, id=item.id)


@admin_bp.route("/catalogs/lab-test", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_lab_test():
    data = request.get_json(silent=True) or request.form
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not code or not name:
        return jsonify(success=False, error="Code and name are required."), 400
    if LabTest.query.filter_by(organization_id=current_user.organization_id, code=code).first():
        return jsonify(success=False, error="That code already exists."), 400
    item = LabTest(
        organization_id=current_user.organization_id,
        code=code, name=name, category=data.get("category"), price=data.get("price") or 0,
    )
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "LabTest", item.id, {"code": code, "name": name})
    db.session.commit()
    return jsonify(success=True, id=item.id)


@admin_bp.route("/catalogs/insurance", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_insurance():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if not name or not code:
        return jsonify(success=False, error="Name and code are required."), 400
    if InsuranceScheme.query.filter_by(organization_id=current_user.organization_id, code=code).first():
        return jsonify(success=False, error="That code already exists."), 400
    item = InsuranceScheme(
        organization_id=current_user.organization_id,
        name=name, code=code, scheme_type=data.get("scheme_type"),
    )
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "InsuranceScheme", item.id, {"code": code, "name": name})
    db.session.commit()
    return jsonify(success=True, id=item.id)


@admin_bp.route("/catalogs/ward", methods=["POST"])
@login_required
@permission_required("catalogs.manage")
def catalogs_add_ward():
    if not current_user.hospital_id:
        return jsonify(success=False, error="Wards are set up per hospital — your account has no hospital assigned."), 400
    if not hospital_allows_inpatient(current_user.hospital):
        return jsonify(success=False, error=f"{current_user.hospital.level} facilities don't offer inpatient admission, so wards aren't applicable."), 400
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    total_beds = data.get("total_beds") or 0
    if not name:
        return jsonify(success=False, error="Ward name is required."), 400
    try:
        total_beds = int(total_beds)
    except ValueError:
        total_beds = 0
    item = Ward(
        hospital_id=current_user.hospital_id, name=name,
        ward_type=data.get("ward_type"), total_beds=total_beds,
        daily_rate=data.get("daily_rate") or 0,
    )
    db.session.add(item)
    db.session.flush()
    log_action(current_user, "create", "Ward", item.id, {"name": name, "total_beds": total_beds})
    db.session.commit()
    return jsonify(success=True, id=item.id)


# ---------------------------------------------------------------------------
# Audit log — read-only view over every AuditLog row this user is allowed
# to see. CEOs (organization scope) see everything logged against their
# organization; everyone else is restricted to their assigned hospital(s),
# same as the rest of the app.
# ---------------------------------------------------------------------------

@admin_bp.route("/audit-logs", methods=["GET"])
@login_required
@permission_required("audit.view")
def audit_logs():
    query = AuditLog.query

    if current_user.role.scope == "organization":
        org_hospital_ids = [
            h.id for h in Hospital.query.filter_by(organization_id=current_user.organization_id).all()
        ]
        org_user_ids = db.session.query(User.id).filter_by(organization_id=current_user.organization_id)
        query = query.filter(
            db.or_(
                AuditLog.hospital_id.in_(org_hospital_ids) if org_hospital_ids else False,
                AuditLog.user_id.in_(org_user_ids),
            )
        )
    else:
        query = query.filter(AuditLog.hospital_id.in_(current_user.accessible_hospital_ids()))

    action = (request.args.get("action") or "").strip()
    model_name = (request.args.get("model") or "").strip()
    q_user = (request.args.get("user") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()

    if action:
        query = query.filter(AuditLog.action == action)
    if model_name:
        query = query.filter(AuditLog.model_name == model_name)
    if q_user:
        like = f"%{q_user}%"
        query = query.join(User, AuditLog.user_id == User.id).filter(
            db.or_(User.username.ilike(like), User.full_name.ilike(like))
        )
    if date_from:
        try:
            query = query.filter(AuditLog.timestamp >= datetime.datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.datetime.strptime(date_to, "%Y-%m-%d") + datetime.timedelta(days=1)
            query = query.filter(AuditLog.timestamp < end)
        except ValueError:
            pass

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)

    distinct_actions = [
        r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    ]
    distinct_models = [
        r[0] for r in db.session.query(AuditLog.model_name)
        .filter(AuditLog.model_name.isnot(None)).distinct().order_by(AuditLog.model_name).all()
    ]

    return render_template(
        "admin/audit_logs.html",
        pagination=pagination, entries=pagination.items,
        distinct_actions=distinct_actions, distinct_models=distinct_models,
        filters={
            "action": action, "model": model_name, "user": q_user,
            "date_from": date_from, "date_to": date_to,
        },
    )
