"""RFC 6238 time-based one-time passwords + single-use backup codes.
Pure standard library, so it has no extra dependencies and is easy to test."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def generate_secret(nbytes=20):
    return base64.b32encode(secrets.token_bytes(nbytes)).decode().rstrip("=")


def _hotp(secret, counter, digits=6):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp(secret, for_time=None, step=30, digits=6):
    t = time.time() if for_time is None else for_time
    return _hotp(secret, int(t // step), digits)


def verify(secret, code, for_time=None, step=30, window=1, digits=6, last_used_step=None):
    """Returns the matched time-step (int) or None. `last_used_step` blocks
    replay: a code from an already-used step (or older) is refused."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != digits:
        return None
    t = time.time() if for_time is None else for_time
    current = int(t // step)
    for delta in range(-window, window + 1):
        s = current + delta
        if last_used_step is not None and s <= last_used_step:
            continue
        if hmac.compare_digest(_hotp(secret, s, digits), code):
            return s
    return None


def provisioning_uri(secret, account, issuer):
    label = urllib.parse.quote(f"{issuer}:{account}")
    q = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                "digits": 6, "period": 30})
    return f"otpauth://totp/{label}?{q}"


def generate_backup_codes(n=8):
    codes = []
    for _ in range(n):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_backup_code(code):
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_backup_code(code, pepper):
    return hmac.new(pepper.encode("utf-8"), normalize_backup_code(code).encode(), hashlib.sha256).hexdigest()


def consume_backup_code(code, hashes, pepper):
    """Returns the remaining list of hashes if `code` matched (and removes it),
    else None."""
    h = hash_backup_code(code, pepper)
    remaining, found = [], False
    for x in hashes:
        if not found and hmac.compare_digest(x, h):
            found = True
            continue
        remaining.append(x)
    return remaining if found else None
