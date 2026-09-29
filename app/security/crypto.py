"""Field-level encryption (AES-256-GCM), blind indexes and encrypted backups.

Why this exists (DHA "Encryption" section): identifiers such as national ID,
passport and birth-certificate numbers must not sit in the database as plain
text. This module encrypts them inside the application, so a copied database
file or a leaked backup does not reveal them.

Key handling (the "key management" part):
  * Keys come from DATA_ENCRYPTION_KEYS ("keyid:base64key,keyid2:base64key"),
    never from the database. The first key is used for new writes; older keys
    stay in the list so old data can still be read. Rotate by adding a new key
    at the front, then run `python encrypt_existing_data.py --rotate`.
  * If no keys are configured a key is derived from SECRET_KEY (id "derived").
    That still encrypts, but it is a fallback: the compliance dashboard marks
    it as "partial" because the key is tied to SECRET_KEY.
  * This is an application-level key ring, NOT a hardware/cloud KMS. If DHA
    requires an HSM- or cloud-KMS-backed service, answer that question
    accordingly (see docs).

Stored format:  enc:v1:<keyid>:<urlsafe-base64(nonce(12) + ciphertext + tag(16))>
The key id is authenticated as associated data. Values that do not start with
"enc:v1:" are treated as legacy plain text and returned unchanged, so an
existing database keeps working until encrypt_existing_data.py has run.
"""
import base64
import hashlib
import hmac
import os
import struct
import unicodedata

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

PREFIX = "enc:v1:"
DERIVED_ID = "derived"
_FILE_MAGIC = b"MCBK1"
_CHUNK = 1024 * 1024
_LAST_FLAG = 0x80000000


class CryptoError(Exception):
    pass


class Keyring:
    def __init__(self, keys, active_id, derived=False):
        if not keys or active_id not in keys:
            raise CryptoError("Keyring needs at least one key and a valid active key id.")
        self.keys = keys            # {key_id: 32-byte key}
        self.active_id = active_id
        self.derived = derived      # True when built from SECRET_KEY (fallback)

    def key(self, key_id):
        try:
            return self.keys[key_id]
        except KeyError:
            raise CryptoError(f"Encryption key '{key_id}' is not configured; cannot decrypt this value.")


def parse_key_spec(spec):
    """'k1:BASE64,k2:BASE64' -> ({'k1': bytes, ...}, 'k1'). Raises CryptoError on a bad key."""
    keys, order = {}, []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise CryptoError("DATA_ENCRYPTION_KEYS entries must look like keyid:base64key.")
        key_id, b64 = part.split(":", 1)
        key_id = key_id.strip()
        if not key_id or ":" in key_id or " " in key_id:
            raise CryptoError("Invalid key id in DATA_ENCRYPTION_KEYS.")
        try:
            raw = base64.urlsafe_b64decode(b64.strip() + "=" * (-len(b64.strip()) % 4))
        except Exception:
            raise CryptoError(f"Key '{key_id}' is not valid base64.")
        if len(raw) != 32:
            raise CryptoError(f"Key '{key_id}' must be exactly 32 bytes (AES-256); got {len(raw)}.")
        keys[key_id] = raw
        order.append(key_id)
    return keys, (order[0] if order else None)


def derive_key(secret, info=b"medicore-data-key"):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"medicore-v1", info=info).derive(
        (secret or "").encode("utf-8"))


_cache = {}


def build_keyring(spec, secret_key):
    cache_key = (spec or "", secret_key or "")
    if cache_key in _cache:
        return _cache[cache_key]
    keys, active = parse_key_spec(spec)
    if keys:
        ring = Keyring(keys, active, derived=False)
    else:
        ring = Keyring({DERIVED_ID: derive_key(secret_key)}, DERIVED_ID, derived=True)
    _cache[cache_key] = ring
    return ring


def _cfg(name, default=""):
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return current_app.config.get(name, default)
    except Exception:
        pass
    return os.environ.get(name, default)


def get_keyring():
    return build_keyring(_cfg("DATA_ENCRYPTION_KEYS"), _cfg("SECRET_KEY", "change-this-in-production-please"))


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_str(plaintext, ring=None):
    if plaintext is None:
        return None
    ring = ring or get_keyring()
    if is_encrypted(plaintext):
        return plaintext  # never double-encrypt
    nonce = os.urandom(12)
    aad = ring.active_id.encode()
    ct = AESGCM(ring.key(ring.active_id)).encrypt(nonce, str(plaintext).encode("utf-8"), aad)
    return f"{PREFIX}{ring.active_id}:{base64.urlsafe_b64encode(nonce + ct).decode()}"


def decrypt_str(token, ring=None):
    if token is None:
        return None
    if not is_encrypted(token):
        return token  # legacy plain-text value
    ring = ring or get_keyring()
    try:
        key_id, b64 = token[len(PREFIX):].split(":", 1)
        blob = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        return AESGCM(ring.key(key_id)).decrypt(blob[:12], blob[12:], key_id.encode()).decode("utf-8")
    except CryptoError:
        raise
    except Exception:
        raise CryptoError("Encrypted value could not be authenticated (wrong key or tampered data).")


def needs_rotation(token, ring=None):
    """True if a stored value is plain text or encrypted under a non-active key."""
    ring = ring or get_keyring()
    if token is None or token == "":
        return False
    if not is_encrypted(token):
        return True
    return token[len(PREFIX):].split(":", 1)[0] != ring.active_id


def normalize_identifier(value):
    """Canonical form used for blind indexes: NFKC, upper-case, no spaces or dashes."""
    v = unicodedata.normalize("NFKC", str(value or "")).upper()
    return "".join(ch for ch in v if ch.isalnum())


def _blind_key():
    explicit = _cfg("BLIND_INDEX_KEY")
    if explicit:
        return explicit.encode("utf-8")
    return derive_key(_cfg("SECRET_KEY", "change-this-in-production-please"), info=b"medicore-blind-index")


def blind_index(value, key=None):
    """Deterministic HMAC of a normalised identifier. Lets us find a patient by
    exact national ID without storing the ID in searchable plain text."""
    norm = normalize_identifier(value)
    if not norm:
        return None
    return hmac.new(key or _blind_key(), norm.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Streaming file encryption (used for backups)
# ---------------------------------------------------------------------------

def encrypt_stream(src, dst, ring=None):
    """Encrypt file-like `src` into `dst` in 1 MiB authenticated chunks.
    Chunks are numbered and the last one is flagged, so truncation, reordering
    or tampering is detected on decrypt."""
    ring = ring or get_keyring()
    key_id = ring.active_id.encode()
    prefix = os.urandom(8)
    header = _FILE_MAGIC + bytes([len(key_id)]) + key_id + prefix
    dst.write(header)
    aes = AESGCM(ring.key(ring.active_id))
    counter = 0
    chunk = src.read(_CHUNK)
    while True:
        nxt = src.read(_CHUNK)
        last = not nxt
        nonce = prefix + struct.pack(">I", counter)
        ct = aes.encrypt(nonce, chunk, header + (b"\x01" if last else b"\x00"))
        dst.write(struct.pack(">I", len(ct) | (_LAST_FLAG if last else 0)))
        dst.write(ct)
        if last:
            break
        chunk, counter = nxt, counter + 1


def decrypt_stream(src, dst, ring=None):
    ring = ring or get_keyring()
    magic = src.read(len(_FILE_MAGIC))
    if magic != _FILE_MAGIC:
        raise CryptoError("Not a MediCore encrypted backup file.")
    klen = src.read(1)[0]
    key_id_b = src.read(klen)
    prefix = src.read(8)
    header = magic + bytes([klen]) + key_id_b + prefix
    aes = AESGCM(ring.key(key_id_b.decode()))
    counter, seen_last = 0, False
    while True:
        raw = src.read(4)
        if not raw:
            break
        if seen_last:
            raise CryptoError("Unexpected data after final chunk.")
        (n,) = struct.unpack(">I", raw)
        last = bool(n & _LAST_FLAG)
        ln = n & ~_LAST_FLAG
        ct = src.read(ln)
        if len(ct) != ln:
            raise CryptoError("Backup file is truncated.")
        try:
            dst.write(aes.decrypt(prefix + struct.pack(">I", counter), ct, header + (b"\x01" if last else b"\x00")))
        except Exception:
            raise CryptoError("Backup chunk failed authentication (wrong key or corrupted file).")
        counter += 1
        seen_last = last
    if not seen_last:
        raise CryptoError("Backup file is truncated (missing final chunk).")


# ---------------------------------------------------------------------------
# SQLAlchemy column type
# ---------------------------------------------------------------------------

try:  # kept optional so this module's pure functions can be tested without SQLAlchemy
    from sqlalchemy.types import TypeDecorator, Text

    class EncryptedText(TypeDecorator):
        """Transparent encryption for a string column. Reads tolerate legacy
        plain-text rows; writes always encrypt."""
        impl = Text
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is None or value == "":
                return value
            return encrypt_str(value)

        def process_result_value(self, value, dialect):
            if value is None or value == "":
                return value
            return decrypt_str(value)
except Exception:  # pragma: no cover
    EncryptedText = None
