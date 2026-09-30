from flask import Flask, redirect, url_for, request
from flask_login import current_user

from config import get_config
from app.extensions import db, login_manager, csrf, migrate


def create_app(config_class=None):
    app = Flask(__name__)
    app.config.from_object(config_class or get_config())
    # Custom config classes (tests, scripts) may not define every setting; fall
    # back to the defaults from config.Config so new DHA settings always exist.
    from config import Config as _Defaults
    for _k in dir(_Defaults):
        if _k.isupper() and _k not in app.config:
            app.config[_k] = getattr(_Defaults, _k)

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
    from app.consent.routes import consent_bp
    from app.consent import models as _consent_models  # noqa: F401  (registers tables)

    # ---- Kenya DHA compliance modules ----
    from app.security import models as _sec_models  # noqa: F401
    from app.records import models as _rec_models  # noqa: F401
    from app.reporting import models as _rep_models  # noqa: F401
    from app.hie import models as _hie_models  # noqa: F401
    from app.backup import models as _bk_models  # noqa: F401
    from app.cds import models as _cds_models  # noqa: F401
    from app.terminology import models as _term_models  # noqa: F401
    from app.security import versioning
    versioning.register_defaults()
    from app.security.routes import security_bp
    from app.records.routes import records_bp
    from app.reporting.routes import reporting_bp
    from app.hie.routes import hie_bp
    from app.backup.routes import backup_bp
    from app.compliance.routes import compliance_bp
    from app.terminology.routes import terminology_bp
    from app.summary.routes import summary_bp
    from app.cds.routes import cds_bp
    from app.cli import register_cli

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
    app.register_blueprint(consent_bp)
    app.register_blueprint(security_bp)
    app.register_blueprint(records_bp)
    app.register_blueprint(reporting_bp)
    app.register_blueprint(hie_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(compliance_bp)
    app.register_blueprint(terminology_bp)
    app.register_blueprint(summary_bp)
    app.register_blueprint(cds_bp)
    register_cli(app)
    # The HIE inbound endpoint authenticates with an HMAC signature, not a browser session
    csrf.exempt(hie_bp)

    # Routes that must stay reachable even when an organization's trial has
    # expired and no subscription is active — otherwise nobody could ever
    # pay to get back in, and login/static assets would break too.
    EXEMPT_ENDPOINTS = {
        "auth.login", "auth.logout", "auth.register_organization", "auth.change_password",
        "security.mfa_verify", "security.mfa_setup", "security.mfa_confirm", "security.dha_login", "security.dha_callback",
        "hie.inbound",
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

        # privileged roles (and everyone, if the organization chose that) must
        # enrol in multi-factor authentication before doing anything else
        from app.security import mfa_service
        if mfa_service.must_enrol(current_user):
            return redirect(url_for("security.mfa_setup"))

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

    @app.before_request
    def force_https():
        """Data in transit: when FORCE_HTTPS is on, plain-HTTP requests are
        redirected. Behind a proxy (PythonAnywhere) the scheme comes from
        X-Forwarded-Proto."""
        if not app.config.get("FORCE_HTTPS") or app.testing:
            return None
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        if proto != "https":
            return redirect(request.url.replace("http://", "https://", 1), code=301)
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
            "idle_logout_minutes": app.config["IDLE_LOGOUT_MINUTES"],
        }

    return app
