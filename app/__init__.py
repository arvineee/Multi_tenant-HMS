from flask import Flask, redirect, url_for, request
from flask_login import current_user

from config import get_config
from app.extensions import db, login_manager, csrf, migrate


def create_app(config_class=None):
    app = Flask(__name__)
    app.config.from_object(config_class or get_config())

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.auth.routes import auth_bp
    from app.admin.routes import admin_bp
    from app.main.routes import main_bp
    from app.patients.routes import patients_bp
    from app.clinical.routes import clinical_bp
    from app.api.routes import api_bp
    from app.pharmacy.routes import pharmacy_bp
    from app.billing.routes import billing_bp
    from app.documents.routes import documents_bp
    from app.inpatient.routes import inpatient_bp
    from app.subscription.routes import subscription_bp
    from app.manual.routes import manual_bp
    from app.sysadmin.routes import sysadmin_bp
    from app.legal.routes import legal_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(main_bp)
    app.register_blueprint(patients_bp)
    app.register_blueprint(clinical_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(pharmacy_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(inpatient_bp)
    app.register_blueprint(subscription_bp)
    app.register_blueprint(manual_bp)
    app.register_blueprint(sysadmin_bp)
    app.register_blueprint(legal_bp)

    # Routes that must stay reachable even when an organization's trial has
    # expired and no subscription is active — otherwise nobody could ever
    # pay to get back in, and login/static assets would break too.
    EXEMPT_ENDPOINTS = {
        "auth.login", "auth.logout", "auth.register_organization", "auth.change_password",
        "subscription.status", "subscription.checkout", "subscription.poll_payment",
        "subscription.simulate_payment", "subscription.webhook",
        "manual.download", "static",
        "legal.privacy_policy", "legal.terms_of_service", "legal.refund_policy",
    }

    @app.before_request
    def enforce_account_and_billing_gates():
        if not current_user.is_authenticated:
            return None
        if request.endpoint in EXEMPT_ENDPOINTS:
            return None

        # a brand-new account on its temp password gets sent to set their
        # own before touching anything else, regardless of billing status
        if current_user.must_change_password:
            return redirect(url_for("auth.change_password"))

        # System Maintainer accounts aren't a paying customer's org —
        # they're internal platform staff, sitting under a dedicated
        # placeholder organization that was never meant to carry a
        # trial/subscription at all. Gating them on it was a bug: it has
        # no trial_ends_at, so has_access is always False and every
        # System Maintainer got bounced to the subscription page on
        # login. Platform scope is exempt from this gate entirely.
        if current_user.role.scope == "platform":
            return None

        org = current_user.organization
        if org and not org.has_access:
            return redirect(url_for("subscription.status"))
        return None

    @app.after_request
    def set_security_headers(response):
        # Clickjacking protection — this app performs real clinical/
        # billing actions from buttons, so it must never be embeddable in
        # another site's invisible iframe.
        response.headers["X-Frame-Options"] = "DENY"
        # Stops the browser guessing content types (e.g. treating an
        # uploaded file as HTML/JS and executing it).
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Every page here can carry patient data, and this app is built
        # for shared clinic terminals — the whole point of the 20-minute
        # idle session timeout in config.py. No-store means the browser
        # (and any shared-computer disk cache) never writes a page
        # containing patient data to disk, so hitting "back" after
        # logging out on a shared terminal can't resurrect it either.
        # Excluded for /static/ — those are just CSS/JS/images with
        # nothing patient-related in them, and forcing a re-download of
        # every asset on every request would hurt on the kind of mobile
        # connection a clinic is likely running on.
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.context_processor
    def inject_support_contact():
        # Available in every template (base.html's sidebar links to it)
        # without needing every single route to pass it explicitly.
        return {
            "support_whatsapp_number": app.config["SUPPORT_WHATSAPP_NUMBER"],
            "support_whatsapp_message": "Hi! I need help with MediCore HMIS.",
        }

    return app
