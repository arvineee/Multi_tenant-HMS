"""One-off upgrade for an EXISTING database (no Alembic history in this repo).

Adds the consent tables, the new audit_logs columns and index, the
consent.manage permission, and database triggers that make audit_logs
append-only even against raw SQL. Safe to run more than once, and works on
SQLite, MySQL and PostgreSQL.

Usage:
    python add_dha_tables.py                # everything
    python add_dha_tables.py --no-triggers  # skip the append-only triggers

If the trigger step is refused (MySQL needs the TRIGGER privilege) the rest
still completes; ask your host, or REVOKE UPDATE/DELETE on audit_logs instead.
"""
import sys

from sqlalchemy import inspect, text

NEW_COLS = {
    "patient_id": "INTEGER",
    "outcome": "VARCHAR(10) DEFAULT 'success'",
    "request_path": "VARCHAR(255)",
}
ROLES_GETTING_CONSENT = ["Doctor", "Nurse", "Records Officer", "Facility Operator"]
PATIENT_INDEX = "ix_audit_logs_patient_id"
TRIGGER_MSG = "audit_logs is append-only"


def ensure_columns_and_index(db):
    insp = inspect(db.engine)
    existing = {c["name"] for c in insp.get_columns("audit_logs")}
    for name, ddl in NEW_COLS.items():
        if name not in existing:
            db.session.execute(text(f"ALTER TABLE audit_logs ADD COLUMN {name} {ddl}"))
            print("added audit_logs." + name)
    db.session.commit()
    # Checked independently of the column, so a half-finished earlier run heals.
    insp = inspect(db.engine)
    if PATIENT_INDEX not in {i["name"] for i in insp.get_indexes("audit_logs")}:
        db.session.execute(text(f"CREATE INDEX {PATIENT_INDEX} ON audit_logs (patient_id)"))
        db.session.commit()
        print("added index " + PATIENT_INDEX)


def install_audit_triggers(db):
    """Refuse UPDATE and DELETE on audit_logs at the database level."""
    dialect = db.engine.dialect.name
    conn = db.session
    if dialect == "sqlite":
        for op in ("UPDATE", "DELETE"):
            conn.execute(text(
                f"CREATE TRIGGER IF NOT EXISTS audit_logs_no_{op.lower()} BEFORE {op} ON audit_logs "
                f"BEGIN SELECT RAISE(ABORT, '{TRIGGER_MSG}'); END"))
    elif dialect in ("mysql", "mariadb"):
        for op in ("UPDATE", "DELETE"):
            name = f"audit_logs_no_{op.lower()}"
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.triggers "
                "WHERE trigger_schema = DATABASE() AND trigger_name = :n"), {"n": name}).scalar()
            if not exists:
                conn.execute(text(
                    f"CREATE TRIGGER {name} BEFORE {op} ON audit_logs FOR EACH ROW "
                    f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{TRIGGER_MSG}'"))
    elif dialect == "postgresql":
        conn.execute(text(
            "CREATE OR REPLACE FUNCTION audit_logs_block() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{TRIGGER_MSG}'; END; $$ LANGUAGE plpgsql"))
        for op in ("UPDATE", "DELETE"):
            name = f"audit_logs_no_{op.lower()}"
            conn.execute(text(f"DROP TRIGGER IF EXISTS {name} ON audit_logs"))
            conn.execute(text(
                f"CREATE TRIGGER {name} BEFORE {op} ON audit_logs "
                "FOR EACH ROW EXECUTE FUNCTION audit_logs_block()"))
    else:
        print(f"! no trigger support coded for {dialect}; REVOKE UPDATE/DELETE on audit_logs instead")
        return
    conn.commit()
    print("audit_logs triggers in place")


def ensure_permission(db):
    from app.models import Permission, Role
    perm = Permission.query.filter_by(code="consent.manage").first()
    if not perm:
        perm = Permission(code="consent.manage", module="consent",
                          description="Record patient consent and authorised persons")
        db.session.add(perm)
        db.session.flush()
    for rname in ROLES_GETTING_CONSENT:
        role = Role.query.filter_by(name=rname).first()
        if not role:
            print(f"! role '{rname}' not found - re-run this script after seeding it")
        elif perm not in role.permissions:
            role.permissions.append(perm)
    db.session.commit()


def main(argv):
    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        db.create_all()  # creates patient_consents / authorised_persons if missing
        ensure_columns_and_index(db)
        ensure_permission(db)
        if "--no-triggers" not in argv:
            try:
                install_audit_triggers(db)
            except Exception as e:  # e.g. MySQL without TRIGGER privilege
                db.session.rollback()
                print(f"! could not install audit triggers ({e.__class__.__name__}: {e}).")
                print("  Everything else was applied. REVOKE UPDATE/DELETE on audit_logs instead.")
        print("done")


if __name__ == "__main__":
    main(sys.argv[1:])
