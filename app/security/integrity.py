"""Tamper evidence for the audit trail.

Each audit row stores entry_hash = HMAC(key, content + prev_hash). The database
triggers/ORM guards stop edits; this lets us *prove* nothing was altered or
removed even if someone bypassed them (e.g. raw SQL with a privileged account).

Honest limits: the HMAC key lives on the application server, so an attacker who
controls both the database and the app's environment could re-seal a rewritten
chain. Keep AUDIT_HMAC_KEY out of the database host, and periodically export
the latest entry_hash somewhere independent (the compliance page shows it).
Under concurrent writers two rows can share the same predecessor; that is not
treated as tampering as long as the predecessor exists.
"""
import hashlib
import hmac
import json

from app.security import crypto


def _key():
    explicit = crypto._cfg("AUDIT_HMAC_KEY")
    if explicit:
        return explicit.encode("utf-8")
    return crypto.derive_key(crypto._cfg("SECRET_KEY", "change-this-in-production-please"),
                             info=b"medicore-audit-chain")


def _iso(ts):
    return ts.replace(microsecond=0).isoformat() if ts is not None else None


def audit_fields(row):
    """Ordered content fields of an audit row (works for ORM objects or dicts)."""
    g = (lambda k: row[k]) if isinstance(row, dict) else (lambda k: getattr(row, k))
    return [g("user_id"), g("hospital_id"), g("action"), g("model_name"), g("record_id"),
            g("details"), g("ip_address"), _iso(g("timestamp")), g("patient_id"),
            g("outcome"), g("request_path")]


def seal(fields, prev_hash, key=None):
    msg = json.dumps([fields, prev_hash], separators=(",", ":"), ensure_ascii=True, default=str)
    return hmac.new(key or _key(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_chain(rows, key=None):
    """`rows`: iterable of dicts (audit fields + prev_hash, entry_hash + id),
    ordered by id ascending. Returns a summary dict."""
    seen = set()
    out = {"total": 0, "sealed": 0, "legacy": 0, "tampered": [], "broken_links": [], "last_hash": None}
    for r in rows:
        out["total"] += 1
        if not r.get("entry_hash"):
            out["legacy"] += 1
            continue
        out["sealed"] += 1
        if not hmac.compare_digest(seal(audit_fields(r), r.get("prev_hash"), key), r["entry_hash"]):
            out["tampered"].append(r["id"])
        prev = r.get("prev_hash")
        if prev is not None and prev not in seen:
            out["broken_links"].append(r["id"])
        seen.add(r["entry_hash"])
        out["last_hash"] = r["entry_hash"]
    out["ok"] = not out["tampered"] and not out["broken_links"]
    return out
