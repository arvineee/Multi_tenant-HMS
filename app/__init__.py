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

    # Routes that must stay reachable even when an organization's trial has
    # expired and no subscription is active — otherwise nobody could ever
    # pay to get back in, and login/static assets would break too.
    EXEMPT_ENDPOINTS = {
        "auth.login", "auth.logout", "auth.register_organization", "auth.change_password",
        "subscription.status", "subscription.checkout", "subscription.poll_payment",
        "subscription.simulate_payment", "subscription.webhook",
        "manual.download", "static",
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

        org = current_user.organization
        if org and not org.has_access:
            return redirect(url_for("subscription.status"))
        return None

    return app
