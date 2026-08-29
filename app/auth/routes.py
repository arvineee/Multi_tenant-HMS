import datetime

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db, csrf
from app.models import User, Organization, Hospital, Role, HOSPITAL_LEVELS, TRIAL_DAYS, log_action
from app.onboarding import seed_starter_catalog_for_org

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
@csrf.exempt  # login happens before we have a session-bound CSRF token to compare against
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "GET":
        return render_template("auth/login.html")

    # AJAX POST: expects JSON {username, password}
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = User.query.filter(
        (User.username == username) | (User.email == username)
    ).first()

    # Checked before the password so a locked-out account never reaches
    # check_password_hash at all — there's nothing to gain from doing the
    # (deliberately slow) hash comparison on an attempt we're rejecting
    # regardless of whether the password is right.
    if user and user.is_locked_out:
        return jsonify(success=False, error="Too many failed attempts. Try again later."), 429

    if not user or not user.check_password(password):
        if user:
            just_locked = user.register_failed_login(
                current_app.config["LOGIN_MAX_ATTEMPTS"], current_app.config["LOGIN_LOCKOUT_MINUTES"]
            )
            log_action(user, "login_locked" if just_locked else "login_failed", ip_address=request.remote_addr)
            db.session.commit()
            if just_locked:
                return jsonify(
                    success=False,
                    error=f"Too many failed attempts. Account locked for {current_app.config['LOGIN_LOCKOUT_MINUTES']} minutes.",
                ), 429
        else:
            # No matching account — nothing to lock (locking on a made-up
            # username would let someone lock out an account they don't
            # even know exists to begin with; there's no real account
            # here to protect), but still worth a trace of the attempt.
            log_action(None, "login_failed", details={"username_attempted": username}, ip_address=request.remote_addr)
            db.session.commit()
        return jsonify(success=False, error="Invalid username or password."), 401

    if not user.is_active:
        return jsonify(success=False, error="This account has been deactivated. Contact your admin."), 403

    user.register_successful_login()
    login_user(user)
    session.permanent = True
    log_action(user, "login", ip_address=request.remote_addr)
    db.session.commit()

    return jsonify(
        success=True,
        redirect=url_for("main.dashboard"),
        must_change_password=user.must_change_password,
    )


@auth_bp.route("/logout")
@login_required
def logout():
    log_action(current_user, "logout", ip_address=request.remote_addr)
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Every new staff account is created with must_change_password=True
    and a temporary password the admin who created them knows — this is
    where that gets closed out. The global before_request gate in
    app/__init__.py redirects here automatically until it's done, so
    nobody can wander the app on a temp password indefinitely."""
    if request.method == "GET":
        return render_template("auth/change_password.html", forced=current_user.must_change_password)

    data = request.get_json(silent=True) or request.form
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not current_user.check_password(current_password):
        return jsonify(success=False, error="Current password is incorrect."), 400
    if len(new_password) < 8:
        return jsonify(success=False, error="New password must be at least 8 characters."), 400
    if new_password != confirm_password:
        return jsonify(success=False, error="New passwords don't match."), 400
    if current_user.check_password(new_password):
        return jsonify(success=False, error="New password must be different from your current password."), 400

    current_user.set_password(new_password)
    current_user.must_change_password = False
    log_action(current_user, "update", "User", current_user.id, {"password_changed": True})
    db.session.commit()

    return jsonify(success=True, redirect=url_for("main.dashboard"))


@auth_bp.route("/register", methods=["GET", "POST"])
@csrf.exempt  # same reasoning as login — no session-bound token exists yet for a brand-new visitor
def register_organization():
    """Self-service signup for a totally new, unrelated hospital group.
    Creates an isolated Organization + first Hospital + founding CEO
    account, with its own starter formulary/lab menu/radiology menu/
    insurance list/wards — completely separate from every other
    organization already on this install."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "GET":
        return render_template("auth/register.html", levels=HOSPITAL_LEVELS)

    data = request.get_json(silent=True) or request.form

    org_name = (data.get("org_name") or "").strip()
    hospital_name = (data.get("hospital_name") or "").strip()
    hospital_code = (data.get("hospital_code") or "").strip().upper()
    hospital_level = data.get("hospital_level") or "Level 4"
    county = (data.get("county") or "").strip()

    admin_full_name = (data.get("admin_full_name") or "").strip()
    admin_username = (data.get("admin_username") or "").strip()
    admin_email = (data.get("admin_email") or "").strip()
    admin_password = data.get("admin_password") or ""

    required = [org_name, hospital_name, hospital_code, admin_full_name, admin_username, admin_email, admin_password]
    if not all(required):
        return jsonify(success=False, error="All fields are required."), 400
    if len(admin_password) < 8:
        return jsonify(success=False, error="Password must be at least 8 characters."), 400
    if User.query.filter((User.username == admin_username) | (User.email == admin_email)).first():
        return jsonify(success=False, error="That username or email is already taken."), 400

    ceo_role = Role.query.filter_by(name="CEO").first()
    if not ceo_role:
        return jsonify(success=False, error="System isn't fully set up yet — contact support."), 500

    org = Organization(
        name=org_name, plan_level=hospital_level,
        subscription_status="trial", trial_ends_at=datetime.datetime.utcnow() + datetime.timedelta(days=TRIAL_DAYS),
    )
    db.session.add(org)
    db.session.flush()

    hospital = Hospital(
        organization_id=org.id, name=hospital_name, code=hospital_code,
        level=hospital_level, county=county or None,
    )
    db.session.add(hospital)
    db.session.flush()

    # the founding user gets hospital_id set too (even though CEO's
    # organization-wide scope doesn't strictly require it) so Settings —
    # which looks up current_user.hospital — works immediately
    admin_user = User(
        organization_id=org.id, hospital_id=hospital.id, role_id=ceo_role.id,
        username=admin_username, email=admin_email, full_name=admin_full_name,
        must_change_password=False,
    )
    admin_user.set_password(admin_password)
    db.session.add(admin_user)
    db.session.flush()

    seed_starter_catalog_for_org(org, hospital)
    log_action(admin_user, "create", "Organization", org.id, {"name": org_name})
    db.session.commit()

    login_user(admin_user)
    session.permanent = True

    return jsonify(success=True, redirect=url_for("main.dashboard"))
