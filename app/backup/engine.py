"""Backup engine: consistent snapshot -> encrypt -> checksum -> offsite copy ->
restore-verify. Pure functions over file paths (no Flask), so they are testable.

SQLite uses the online backup API (safe while the app is running). MySQL and
PostgreSQL shell out to mysqldump / pg_dump, which must be installed on the
host. Backups are encrypted with the same key ring as field encryption
(app/security/crypto.py); keep the keys somewhere OTHER than the backup
location, or the backups cannot be restored.
"""
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile

from app.security import crypto


class BackupError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sqlite_path_from_uri(uri):
    if not uri.startswith("sqlite:///") or uri.startswith("sqlite:///:memory:"):
        raise BackupError("Not a file-based SQLite database.")
    return uri[len("sqlite:///"):]


def snapshot_sqlite(db_file, dest):
    src = sqlite3.connect(db_file)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def table_counts_sqlite(path):
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in sorted(names)}
    finally:
        con.close()


def dump_external(uri, dest):
    """mysqldump / pg_dump to `dest` (plain SQL). Returns the engine name."""
    from urllib.parse import urlparse, unquote
    u = urlparse(uri)
    scheme = u.scheme.split("+")[0]
    db = (u.path or "/").lstrip("/")
    if scheme in ("mysql", "mariadb"):
        exe = shutil.which("mysqldump")
        if not exe:
            raise BackupError("mysqldump is not installed on this server.")
        env = dict(os.environ, MYSQL_PWD=unquote(u.password or ""))
        cmd = [exe, "--single-transaction", "--routines", f"--host={u.hostname}", f"--port={u.port or 3306}",
               f"--user={unquote(u.username or '')}", db]
        engine = "mysql"
    elif scheme in ("postgresql", "postgres"):
        exe = shutil.which("pg_dump")
        if not exe:
            raise BackupError("pg_dump is not installed on this server.")
        env = dict(os.environ, PGPASSWORD=unquote(u.password or ""))
        cmd = [exe, "--no-owner", "-h", u.hostname or "localhost", "-p", str(u.port or 5432), "-U", unquote(u.username or ""), db]
        engine = "postgresql"
    else:
        raise BackupError(f"Backups are not implemented for '{scheme}'.")
    with open(dest, "wb") as out:
        r = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, env=env)
    if r.returncode != 0:
        raise BackupError("Dump failed: " + r.stderr.decode("utf-8", "replace")[:300])
    return engine


def run_backup(db_uri, backup_dir, keep_days=35, ring=None, now=None):
    """Creates <backup_dir>/medicore-YYYYmmdd-HHMMSS.<ext>.enc. Returns a result dict."""
    now = now or datetime.datetime.utcnow()
    os.makedirs(backup_dir, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    ring = ring or crypto.get_keyring()
    with tempfile.TemporaryDirectory() as tmp:
        if db_uri.startswith("sqlite"):
            engine, ext = "sqlite", "db"
            raw = os.path.join(tmp, f"snap.{ext}")
            snapshot_sqlite(sqlite_path_from_uri(db_uri), raw)
            counts = table_counts_sqlite(raw)
        else:
            ext = "sql"
            raw = os.path.join(tmp, f"snap.{ext}")
            engine = dump_external(db_uri, raw)
            counts = None
        name = f"medicore-{stamp}.{ext}.enc"
        final = os.path.join(backup_dir, name)
        part = final + ".part"
        with open(raw, "rb") as src, open(part, "wb") as dst:
            crypto.encrypt_stream(src, dst, ring)
        os.replace(part, final)
    try:
        os.chmod(final, 0o600)
    except OSError:
        pass
    result = {"file": final, "file_name": name, "engine": engine, "size": os.path.getsize(final),
              "sha256": sha256_file(final), "key_id": ring.active_id, "table_counts": counts}
    result["pruned"] = prune(backup_dir, keep_days, now)
    return result


def prune(backup_dir, keep_days, now=None):
    now = now or datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(days=keep_days)
    removed = 0
    for n in os.listdir(backup_dir):
        if n.startswith("medicore-") and n.endswith(".enc"):
            p = os.path.join(backup_dir, n)
            if datetime.datetime.utcfromtimestamp(os.path.getmtime(p)) < cutoff:
                os.remove(p)
                removed += 1
    return removed


def copy_offsite(final_path, offsite_dir=None, s3_bucket=None, s3_prefix="", s3_endpoint=None):
    """Returns a short status string. Local/mounted folder and/or S3-compatible bucket."""
    parts = []
    if offsite_dir:
        os.makedirs(offsite_dir, exist_ok=True)
        dest = os.path.join(offsite_dir, os.path.basename(final_path))
        shutil.copy2(final_path, dest)
        if sha256_file(dest) != sha256_file(final_path):
            raise BackupError("Offsite copy checksum mismatch.")
        parts.append(f"copied to {offsite_dir}")
    if s3_bucket:
        try:
            import boto3
        except ImportError:
            raise BackupError("S3 upload needs `pip install boto3`.")
        kw = {"endpoint_url": s3_endpoint} if s3_endpoint else {}
        boto3.client("s3", **kw).upload_file(final_path, s3_bucket, s3_prefix + os.path.basename(final_path))
        parts.append(f"uploaded to s3://{s3_bucket}")
    return "; ".join(parts) if parts else "not configured"


def verify_backup(final_path, expected_sha256=None, expected_counts=None, ring=None):
    """Checksum, decrypt, and (SQLite) open the restored copy and compare table
    row counts. Returns (ok, message). Never touches the live database."""
    if expected_sha256 and sha256_file(final_path) != expected_sha256:
        return False, "Checksum does not match what was recorded."
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "restored")
        try:
            with open(final_path, "rb") as src, open(out, "wb") as dst:
                crypto.decrypt_stream(src, dst, ring)
        except crypto.CryptoError as e:
            return False, f"Decryption failed: {e}"
        with open(out, "rb") as f:
            head = f.read(16)
        if head.startswith(b"SQLite format 3"):
            try:
                counts = table_counts_sqlite(out)
            except sqlite3.DatabaseError as e:
                return False, f"Restored file is not a usable database: {e}"
            if expected_counts and counts != expected_counts:
                return False, "Restored table counts differ from the recorded snapshot."
            return True, f"Restore test passed ({len(counts)} tables, {sum(counts.values())} rows)."
        return True, "Decrypted and checksum verified (SQL dump; row counts not compared)."


def restore_to(final_path, dest_path, ring=None):
    """Decrypt a backup to `dest_path` (never overwrites without the caller choosing the path)."""
    if os.path.exists(dest_path):
        raise BackupError("Destination exists; choose a new path.")
    with open(final_path, "rb") as src, open(dest_path, "wb") as dst:
        crypto.decrypt_stream(src, dst, ring)
    return dest_path


def latest_backup_age_hours(backup_dir, now=None):
    now = now or datetime.datetime.utcnow()
    if not os.path.isdir(backup_dir):
        return None
    times = [os.path.getmtime(os.path.join(backup_dir, n)) for n in os.listdir(backup_dir)
             if n.startswith("medicore-") and n.endswith(".enc")]
    if not times:
        return None
    return round((now - datetime.datetime.utcfromtimestamp(max(times))).total_seconds() / 3600, 1)
