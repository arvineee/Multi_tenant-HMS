"""Encrypt (or re-encrypt under a new key) patient identifiers already in the database.

    python encrypt_existing_data.py            # encrypt plain-text values + fill blind indexes
    python encrypt_existing_data.py --rotate   # also re-encrypt values under an older key with the active key
    python encrypt_existing_data.py --dry-run  # count only

Take a backup first. Uses raw SQL so it works even before the ORM type is in play,
and commits in small batches so it can be re-run safely after an interruption.
"""
import sys

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.security import crypto

COLS = [("national_id", "national_id_idx"), ("passport_number", "passport_number_idx"),
        ("birth_certificate_number", "birth_certificate_idx")]


def run(rotate=False, dry=False):
    ring = crypto.get_keyring()
    if ring.derived:
        print("NOTE: no DATA_ENCRYPTION_KEYS set - using a key derived from SECRET_KEY. "
              "Set DATA_ENCRYPTION_KEYS and re-run with --rotate to move to a dedicated key.")
    changed = 0
    for col, idx in COLS:
        rows = db.session.execute(text(f"SELECT id, {col} FROM patients WHERE {col} IS NOT NULL AND {col} <> ''")).fetchall()
        for pid, val in rows:
            plain = crypto.decrypt_str(val, ring)
            need = (not crypto.is_encrypted(val)) or (rotate and crypto.needs_rotation(val, ring))
            index = crypto.blind_index(plain)
            if not need and index:
                cur = db.session.execute(text(f"SELECT {idx} FROM patients WHERE id = :i"), {"i": pid}).scalar()
                if cur == index:
                    continue
            changed += 1
            if dry:
                continue
            enc = crypto.encrypt_str(plain, ring) if need else val
            db.session.execute(text(f"UPDATE patients SET {col} = :v, {idx} = :x WHERE id = :i"),
                               {"v": enc, "x": index, "i": pid})
            if changed % 200 == 0:
                db.session.commit()
    db.session.commit()
    print(("would update " if dry else "updated ") + f"{changed} value(s)")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        run(rotate="--rotate" in sys.argv, dry="--dry-run" in sys.argv)
