"""
Seeds the database with:
  - Permissions (the full RBAC permission list)
  - System roles (CEO, Hospital Manager, Doctor, Nurse, Pharmacist, Lab Tech,
    Radiologist, Records Officer, Billing/Insurance Clerk, Admin) and what
    each is allowed to do — these are shared system-wide reference data,
    not tenant-specific, so every organization on this install uses the
    same role/permission definitions
  - One demo Organization + Hospital (their formulary/lab menu/radiology
    menu/insurance list/wards come from app/onboarding.py — the same
    starter data every NEW organization gets via self-service signup)
  - One login per role so you can test access levels immediately
  - The full ICD-10-CM diagnosis catalog is loaded separately —
    run import_icd10.py after this (see README)

Run with:  python seed.py

New, unrelated hospital groups do NOT need this script — they sign up at
/auth/register and get their own isolated Organization, Hospital, and
starter catalog automatically.
"""
from app import create_app
from app.extensions import db
from app.models import Organization, Hospital, Role, Permission, User
from app.onboarding import seed_starter_catalog_for_org
import datetime

app = create_app()

PERMISSIONS = [
    # (code, module, description)
    ("settings.view", "settings", "View hospital settings"),
    ("settings.edit", "settings", "Edit hospital settings"),
    ("hospitals.manage", "hospitals", "Create/manage hospitals & branches (CEO/superadmin)"),
    ("users.manage", "users", "Create/manage staff accounts and roles"),
    ("catalogs.manage", "catalogs", "Manage diagnosis, drug, radiology & insurance catalogs"),
    ("reports.view_all_hospitals", "reports", "View reports across every hospital in the organization"),
    ("reports.view_own_hospital", "reports", "View reports for own hospital only"),
    ("patient.register", "patients", "Register new patients"),
    ("patient.view", "patients", "View patient records"),
    ("triage.create", "clinical", "Record triage vitals"),
    ("consultation.create", "clinical", "Record consultations, diagnoses & inpatient admission/discharge"),
    ("prescription.create", "pharmacy", "Prescribe drugs"),
    ("pharmacy.dispense", "pharmacy", "Dispense drugs against a prescription"),
    ("pharmacy.stock", "pharmacy", "Receive and manage pharmacy stock batches"),
    ("radiology.order", "radiology", "Order radiology/imaging tests"),
    ("radiology.report", "radiology", "Enter radiology findings/reports"),
    ("lab.order", "lab", "Order lab tests"),
    ("lab.result", "lab", "Enter lab results"),
    ("billing.manage", "billing", "Manage billing and insurance claims"),
    ("audit.view", "audit", "View the audit log (who did what, when)"),
    ("pricing.manage", "settings", "Manage pricing & money-related system settings — consultation fee, low-stock threshold, insurance authorization defaults (system owner only)"),
    ("system.maintain", "system", "Full cross-hospital system access plus the diagnostics/maintenance dashboard — system maintenance staff only, never assignable from the ordinary user-management screen"),
]

# role_name -> (scope, [permission codes])
ROLES = {
    "CEO": ("organization", [
        "settings.view", "settings.edit", "hospitals.manage", "users.manage", "catalogs.manage",
        "reports.view_all_hospitals", "audit.view", "pricing.manage",
    ]),
    "Hospital Manager": ("hospital", [
        "settings.view", "settings.edit", "users.manage", "reports.view_own_hospital",
        "patient.view", "audit.view",
    ]),
    "Admin": ("hospital", [
        "settings.view", "settings.edit", "users.manage", "catalogs.manage", "audit.view",
    ]),
    "Doctor": ("department", [
        "patient.view", "patient.register", "consultation.create",
        "prescription.create", "radiology.order", "lab.order",
    ]),
    "Nurse": ("department", [
        "patient.view", "patient.register", "triage.create",
    ]),
    "Pharmacist": ("department", [
        "patient.view", "pharmacy.dispense", "pharmacy.stock",
    ]),
    "Lab Technician": ("department", [
        "patient.view", "lab.result",
    ]),
    "Radiologist": ("department", [
        "patient.view", "radiology.report",
    ]),
    "Records Officer": ("department", [
        "patient.view", "patient.register",
    ]),
    "Billing / Insurance Clerk": ("department", [
        "patient.view", "billing.manage",
    ]),
    # For a Level 1 (community unit) or Level 2 (dispensary) facility
    # commonly staffed by just one or two people — a single clinical
    # officer/nurse who registers the patient, triages, consults,
    # prescribes, dispenses, orders and reads back basic labs/X-ray, and
    # bills, all themselves. Bundles the full front-line operational
    # permission set into one role rather than needing five separate
    # logins for a facility that only has one person on shift.
    # Deliberately excludes admin-level permissions (catalogs, settings,
    # users, pricing) — those stay on the CEO account set up at
    # registration, since they're setup tasks, not daily operations.
    # Restricted at assignment time (admin/routes.py:users_create) to
    # hospitals whose level is actually Level 1 or Level 2 — the
    # inpatient/advanced-radiology ceiling for those levels is already
    # enforced separately by app/level_policy.py regardless of what this
    # role can do, so there's no risk of it granting more than a small
    # facility is actually allowed to perform.
    "Facility Operator": ("hospital", [
        "patient.view", "patient.register",
        "triage.create",
        "consultation.create", "prescription.create",
        "lab.order", "lab.result",
        "radiology.order", "radiology.report",
        "pharmacy.dispense", "pharmacy.stock",
        "billing.manage",
    ]),
    # Platform-level maintenance access — sees every hospital across every
    # organization, plus the system diagnostics dashboard. Deliberately
    # its own scope ("platform"), separate from "organization" (CEO):
    # a clinic's own CEO/Admin should never be able to grant this to
    # anyone, including themselves — see the guard in
    # admin/routes.py:users_create(). There is intentionally no web UI
    # path to assign this role; the first account is created with
    # create_system_maintainer.py, run directly on the server.
    "System Maintainer": ("platform", [
        "system.maintain",
    ]),
}


def seed_roles_and_permissions():
    perm_map = {}
    for code, module, desc in PERMISSIONS:
        perm = Permission.query.filter_by(code=code).first()
        if not perm:
            perm = Permission(code=code, module=module, description=desc)
            db.session.add(perm)
        perm_map[code] = perm
    db.session.commit()

    role_map = {}
    for name, (scope, perm_codes) in ROLES.items():
        role = Role.query.filter_by(name=name).first()
        if not role:
            role = Role(name=name, scope=scope, is_system=True)
            db.session.add(role)
            db.session.flush()
        role.scope = scope
        role.permissions = [perm_map[c] for c in perm_codes]
        role_map[name] = role
    db.session.commit()
    return role_map


def run():
    with app.app_context():
        db.create_all()

        role_map = seed_roles_and_permissions()

        # --- Demo organization + hospital ---
        org = Organization.query.filter_by(name="Demo Health Group").first()
        is_new_org = org is None
        if is_new_org:
            org = Organization(
                name="Demo Health Group", plan_level="Level 4",
                subscription_status="active",
                current_period_end=datetime.datetime.utcnow() + datetime.timedelta(days=3650),
            )
            db.session.add(org)
            db.session.flush()

        hospital = Hospital.query.filter_by(organization_id=org.id, code="NRB-01").first()
        if not hospital:
            hospital = Hospital(
                organization_id=org.id,
                name="Demo Level 4 Hospital - Nairobi",
                code="NRB-01",
                level="Level 4",
                county="Nairobi",
                phone="0700000000",
                email="info@demohospital.example",
            )
            db.session.add(hospital)
            db.session.flush()

        # --- One demo login per role. CEO gets hospital_id set too (even
        # though CEO's organization-wide access doesn't strictly need it)
        # so the Settings page — which looks up current_user.hospital —
        # works for them out of the box. ---
        demo_users = [
            ("ceo", "ceo@demo.example", "CEO", "Grace Mwangi", hospital.id),
            ("manager", "manager@demo.example", "Hospital Manager", "Peter Otieno", hospital.id),
            ("admin", "admin@demo.example", "Admin", "System Admin", hospital.id),
            ("doctor", "doctor@demo.example", "Doctor", "Dr. Amina Yusuf", hospital.id),
            ("nurse", "nurse@demo.example", "Nurse", "Nurse Faith Kamau", hospital.id),
            ("pharmacist", "pharmacist@demo.example", "Pharmacist", "James Mburu", hospital.id),
            ("labtech", "labtech@demo.example", "Lab Technician", "Susan Wambui", hospital.id),
            ("radiologist", "radiologist@demo.example", "Radiologist", "Dr. Kevin Njoroge", hospital.id),
            ("records", "records@demo.example", "Records Officer", "Mercy Achieng", hospital.id),
            ("billing", "billing@demo.example", "Billing / Insurance Clerk", "Brian Kiptoo", hospital.id),
        ]

        for username, email, role_name, full_name, hosp_id in demo_users:
            if User.query.filter_by(username=username).first():
                continue
            user = User(
                organization_id=org.id,
                hospital_id=hosp_id,
                role_id=role_map[role_name].id,
                username=username,
                email=email,
                full_name=full_name,
                # demo/evaluation accounts skip the forced first-login
                # password change — that's for real staff onboarding
                # (see Admin -> Staff & Roles), not exploring the demo
                must_change_password=False,
            )
            user.set_password("Password123!")
            db.session.add(user)

        db.session.commit()

        # --- Starter catalog (formulary, lab menu, radiology menu,
        # insurance list, wards) — only seed once per organization ---
        from app.models import Drug
        if not Drug.query.filter_by(organization_id=org.id).first():
            seed_starter_catalog_for_org(org, hospital)
            db.session.commit()

        print("Seed complete.")
        print("Demo logins (all use password: Password123!):")
        for username, email, role_name, full_name, hosp_id in demo_users:
            print(f"  {username:12} -> {role_name}")
        print()
        print("Diagnosis catalog not loaded by this script — run:")
        print("  python import_icd10.py icd10_source.xlsx --replace")


if __name__ == "__main__":
    run()
