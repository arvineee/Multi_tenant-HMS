"""
Creates (or resets the password for) a System Maintainer account —
platform-wide access plus the /system-maintenance diagnostics dashboard.

This is deliberately a command-line script, not a web form. There is no
page anywhere in the app that can grant the "System Maintainer" role —
not even to a CEO account — by design: it's the one role that sees every
hospital across every organization, so the only way to create one is to
already have direct access to the server.

Run:
    python create_system_maintainer.py

You'll be prompted for a username, email, full name, and password (the
password is entered with getpass, so it never appears on screen or in
shell history). Re-running with a username that already exists just
resets that account's password instead of creating a duplicate.

Before running this the first time, make sure seed.py has been run (or
re-run) so the "System Maintainer" role and "system.maintain" permission
exist — this script will tell you plainly if they don't, rather than
half-creating something.
"""
import getpass

from app import create_app
from app.extensions import db
from app.models import Organization, Role, User

app = create_app()

MAINTENANCE_ORG_NAME = "System Maintenance"


def run():
    with app.app_context():
        role = Role.query.filter_by(name="System Maintainer").first()
        if not role or role.scope != "platform":
            print("The 'System Maintainer' role doesn't exist yet (or isn't set up correctly).")
            print("Run `python seed.py` first, then re-run this script.")
            return

        # A dedicated placeholder organization to house maintainer
        # accounts, rather than attaching one to any real clinic's
        # organization — keeps it out of any customer's own user list
        # and reports.
        org = Organization.query.filter_by(name=MAINTENANCE_ORG_NAME).first()
        if not org:
            # subscription_status="purchased" means Organization.has_access
            # is always True with no expiry to track — this org was never
            # meant to be gated by a trial/subscription at all (the actual
            # fix for that is the platform-scope exemption in
            # app/__init__.py's before_request gate; this is just defense
            # in depth in case anything else ever checks org.has_access).
            org = Organization(name=MAINTENANCE_ORG_NAME, plan_level="Level 4", subscription_status="purchased")
            db.session.add(org)
            db.session.flush()
            print(f"Created placeholder organization '{MAINTENANCE_ORG_NAME}'.")

        username = input("Username: ").strip()
        if not username:
            print("Username is required.")
            return

        existing = User.query.filter_by(username=username).first()
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords didn't match. Nothing changed.")
            return
        if len(password) < 8:
            print("Use at least 8 characters.")
            return

        if existing:
            if existing.role_id != role.id:
                print(f"'{username}' exists but isn't a System Maintainer (role: {existing.role.name}). Not touching it.")
                return
            existing.set_password(password)
            existing.must_change_password = False
            db.session.commit()
            print(f"Password reset for existing System Maintainer '{username}'.")
            return

        email = input("Email: ").strip()
        full_name = input("Full name: ").strip()
        if not email or not full_name:
            print("Email and full name are required.")
            return

        user = User(
            organization_id=org.id, hospital_id=None, role_id=role.id,
            username=username, email=email, full_name=full_name,
            is_active=True, must_change_password=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Created System Maintainer account '{username}'.")


if __name__ == "__main__":
    run()
