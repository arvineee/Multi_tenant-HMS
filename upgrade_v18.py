"""One-off upgrade of an EXISTING MediCore database to v18 (DHA compliance).

Safe to run more than once. Works on SQLite, MySQL and PostgreSQL. Take a copy
of your database first (this repo has no Alembic history, so schema changes
are applied here).

    python upgrade_v18.py                 # everything
    python upgrade_v18.py --no-triggers   # skip append-only triggers on record_versions
    python upgrade_v18.py --encrypt       # also encrypt existing identifiers (needs keys configured first)
"""
import sys

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db

# table -> {column: ddl}
NEW_COLUMNS = {
    "hospitals": {"sub_county": "VARCHAR(100)", "mfl_code": "VARCHAR(20)"},
    "users": {"health_worker_id": "VARCHAR(50)", "professional_license_no": "VARCHAR(50)", "cadre": "VARCHAR(60)"},
    "audit_logs": {"prev_hash": "VARCHAR(64)", "entry_hash": "VARCHAR(64)"},
    "diagnosis_codes": {"icd11_code": "VARCHAR(30)", "snomed_ct_code": "VARCHAR(20)"},
    "drugs": {"hpt_code": "VARCHAR(50)", "atc_code": "VARCHAR(10)", "pregnancy_caution": "BOOLEAN DEFAULT 0",
              "min_age_months": "INTEGER"},
    "radiology_tests": {"loinc_code": "VARCHAR(20)", "snomed_ct_code": "VARCHAR(20)"},
    "lab_tests": {"loinc_code": "VARCHAR(20)", "snomed_ct_code": "VARCHAR(20)", "unit": "VARCHAR(30)",
                  "ref_low": "FLOAT", "ref_high": "FLOAT", "critical_low": "FLOAT", "critical_high": "FLOAT"},
    "patients": {"id_type": "VARCHAR(30)", "national_id_idx": "VARCHAR(64)", "passport_number": "TEXT",
                 "passport_number_idx": "VARCHAR(64)", "birth_certificate_number": "TEXT",
                 "birth_certificate_idx": "VARCHAR(64)", "dha_client_id": "VARCHAR(60)", "nationality": "VARCHAR(60)",
                 "email": "VARCHAR(120)", "county": "VARCHAR(100)", "sub_county": "VARCHAR(100)", "ward": "VARCHAR(100)",
                 "village": "VARCHAR(100)", "allergy_status": "VARCHAR(30) DEFAULT 'Unknown'",
                 "allergy_status_recorded_at": "DATETIME"},
    "visits": {"outcome": "VARCHAR(20)"},
    "triage_records": {"head_circumference_cm": "FLOAT", "muac_cm": "FLOAT"},
}
INDEXES = [("patients", "national_id_idx"), ("patients", "passport_number_idx"), ("patients", "birth_certificate_idx"),
           ("patients", "dha_client_id")]


def _ddl_for(dialect, ddl):
    if dialect == "postgresql":
        return ddl.replace("DATETIME", "TIMESTAMP").replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
    return ddl


def add_columns():
    insp = inspect(db.engine)
    dialect = db.engine.dialect.name
    tables = set(insp.get_table_names())
    for table, cols in NEW_COLUMNS.items():
        if table not in tables:
            print(f"! table {table} not found; skipping")
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in existing:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {_ddl_for(dialect, ddl)}"))
                print(f"added {table}.{name}")
    db.session.commit()


def widen_encrypted_column():
    """patients.national_id was VARCHAR(30); ciphertext is longer."""
    dialect = db.engine.dialect.name
    if dialect in ("mysql", "mariadb"):
        db.session.execute(text("ALTER TABLE patients MODIFY national_id TEXT"))
    elif dialect == "postgresql":
        db.session.execute(text("ALTER TABLE patients ALTER COLUMN national_id TYPE TEXT"))
    db.session.commit()  # SQLite does not enforce VARCHAR length


def add_indexes():
    insp = inspect(db.engine)
    for table, col in INDEXES:
        name = f"ix_{table}_{col}"
        if name not in {i["name"] for i in insp.get_indexes(table)}:
            db.session.execute(text(f"CREATE INDEX {name} ON {table} ({col})"))
            print("added index " + name)
    db.session.commit()


def install_version_triggers():
    dialect = db.engine.dialect.name
    msg = "record_versions is append-only"
    if dialect == "sqlite":
        for op in ("UPDATE", "DELETE"):
            db.session.execute(text(
                f"CREATE TRIGGER IF NOT EXISTS record_versions_no_{op.lower()} BEFORE {op} ON record_versions "
                f"BEGIN SELECT RAISE(ABORT, '{msg}'); END"))
    elif dialect in ("mysql", "mariadb"):
        for op in ("UPDATE", "DELETE"):
            name = f"record_versions_no_{op.lower()}"
            exists = db.session.execute(text(
                "SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema = DATABASE() AND trigger_name = :n"),
                {"n": name}).scalar()
            if not exists:
                db.session.execute(text(f"CREATE TRIGGER {name} BEFORE {op} ON record_versions FOR EACH ROW "
                                        f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{msg}'"))
    elif dialect == "postgresql":
        db.session.execute(text(
            f"CREATE OR REPLACE FUNCTION record_versions_guard() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION '{msg}'; END; $$ LANGUAGE plpgsql"))
        db.session.execute(text("DROP TRIGGER IF EXISTS record_versions_no_change ON record_versions"))
        db.session.execute(text("CREATE TRIGGER record_versions_no_change BEFORE UPDATE OR DELETE ON record_versions "
                                "FOR EACH ROW EXECUTE FUNCTION record_versions_guard()"))
    db.session.commit()
    print("append-only triggers installed on record_versions")


def main(argv):
    app = create_app()
    with app.app_context():
        db.create_all()          # creates every NEW table; never alters existing ones
        add_columns()
        widen_encrypted_column()
        add_indexes()
        if "--no-triggers" not in argv:
            try:
                install_version_triggers()
            except Exception as e:  # noqa: BLE001
                db.session.rollback()
                print(f"! could not install triggers ({e.__class__.__name__}); everything else completed. "
                      "Ask your host for TRIGGER privilege, or REVOKE UPDATE/DELETE on record_versions.")
        from seed import seed_roles_and_permissions
        from app.reporting.service import seed_notifiable_diseases, seed_quality_measures
        seed_roles_and_permissions()
        print(f"seeded {seed_notifiable_diseases()} notifiable diseases, {seed_quality_measures()} quality measures")
        db.session.commit()
        if "--encrypt" in argv:
            import encrypt_existing_data
            encrypt_existing_data.run()
        print("upgrade complete. Next: set DATA_ENCRYPTION_KEYS, then `python encrypt_existing_data.py`.")


if __name__ == "__main__":
    main(sys.argv[1:])
