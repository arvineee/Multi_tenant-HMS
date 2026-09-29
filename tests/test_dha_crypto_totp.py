"""Pure-logic tests: no database or Flask app needed."""
import base64
import importlib.util
import io
import os
import pathlib
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "security"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", _ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crypto = _load("crypto")
totp = _load("totp")


def _ring(*ids):
    spec = ",".join(f"{i}:{base64.urlsafe_b64encode(os.urandom(32)).decode()}" for i in ids)
    return crypto.build_keyring(spec, "secret"), spec


def test_roundtrip_and_no_plaintext():
    ring, _ = _ring("k1")
    tok = crypto.encrypt_str("37722207", ring)
    assert tok.startswith("enc:v1:k1:") and "37722207" not in tok
    assert crypto.decrypt_str(tok, ring) == "37722207"


def test_random_nonce_and_no_double_encrypt():
    ring, _ = _ring("k1")
    a, b = crypto.encrypt_str("x", ring), crypto.encrypt_str("x", ring)
    assert a != b
    assert crypto.encrypt_str(a, ring) == a


def test_legacy_plaintext_passes_through():
    ring, _ = _ring("k1")
    assert crypto.decrypt_str("12345678", ring) == "12345678"
    assert crypto.needs_rotation("12345678", ring)


def test_tamper_and_wrong_key_detected():
    ring, _ = _ring("k1")
    tok = crypto.encrypt_str("secret", ring)
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_str(bad, ring)
    other, _ = _ring("k1")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_str(tok, other)


def test_key_rotation_reads_old_writes_new():
    ring1, spec1 = _ring("k1")
    tok = crypto.encrypt_str("A123456", ring1)
    k2 = base64.urlsafe_b64encode(os.urandom(32)).decode()
    ring2 = crypto.build_keyring(f"k2:{k2},{spec1}", "secret")
    assert crypto.decrypt_str(tok, ring2) == "A123456"
    assert crypto.needs_rotation(tok, ring2)
    new = crypto.encrypt_str("A123456", ring2)
    assert new.startswith("enc:v1:k2:") and not crypto.needs_rotation(new, ring2)


def test_bad_key_spec_rejected():
    with pytest.raises(crypto.CryptoError):
        crypto.parse_key_spec("k1:" + base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(crypto.CryptoError):
        crypto.parse_key_spec("nocolon")


def test_derived_fallback_flagged():
    ring = crypto.build_keyring("", "some-secret")
    assert ring.derived and ring.active_id == "derived"
    assert crypto.decrypt_str(crypto.encrypt_str("x", ring), ring) == "x"


def test_blind_index_is_stable_and_normalised():
    k = b"k" * 32
    assert crypto.blind_index(" 3772-2207 ", k) == crypto.blind_index("37722207", k)
    assert crypto.blind_index("a123456", k) == crypto.blind_index("A 123456", k)
    assert crypto.blind_index("37722207", k) != crypto.blind_index("37722208", k)
    assert crypto.blind_index("", k) is None


@pytest.mark.parametrize("size", [0, 1, 1024 * 1024, 1024 * 1024 + 5, 3 * 1024 * 1024 + 17])
def test_stream_roundtrip(size):
    ring, _ = _ring("b1")
    data = os.urandom(size)
    enc = io.BytesIO()
    crypto.encrypt_stream(io.BytesIO(data), enc, ring)
    enc.seek(0)
    out = io.BytesIO()
    crypto.decrypt_stream(enc, out, ring)
    assert out.getvalue() == data


def test_stream_truncation_and_tamper_detected():
    ring, _ = _ring("b1")
    data = os.urandom(2 * 1024 * 1024 + 10)
    enc = io.BytesIO()
    crypto.encrypt_stream(io.BytesIO(data), enc, ring)
    blob = enc.getvalue()
    with pytest.raises(crypto.CryptoError):           # cut off the last chunk
        crypto.decrypt_stream(io.BytesIO(blob[: len(blob) // 2]), io.BytesIO(), ring)
    flipped = bytearray(blob); flipped[100] ^= 1
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_stream(io.BytesIO(bytes(flipped)), io.BytesIO(), ring)


def test_totp_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, secret "12345678901234567890", T=59 -> 94287082 (8 digits)
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp.totp(secret, for_time=59, digits=8) == "94287082"
    assert totp.totp(secret, for_time=1111111109, digits=8) == "07081804"


def test_totp_verify_window_and_replay():
    s = totp.generate_secret()
    now = 1_700_000_000
    code = totp.totp(s, for_time=now)
    step = totp.verify(s, code, for_time=now)
    assert step == now // 30
    assert totp.verify(s, code, for_time=now, last_used_step=step) is None      # replay blocked
    assert totp.verify(s, code, for_time=now + 30) == step                     # one step of clock drift ok
    assert totp.verify(s, code, for_time=now + 300) is None                    # too old
    assert totp.verify(s, "12345", for_time=now) is None                       # wrong length
    assert totp.verify(s, "abcdef", for_time=now) is None


def test_backup_codes_single_use():
    codes = totp.generate_backup_codes(4)
    assert len(set(codes)) == 4 and all(len(c) == 11 and c[5] == "-" for c in codes)
    hashes = [totp.hash_backup_code(c, "pep") for c in codes]
    rest = totp.consume_backup_code(codes[1].lower().replace("-", " "), hashes, "pep")
    assert rest is not None and len(rest) == 3
    assert totp.consume_backup_code(codes[1], rest, "pep") is None
    assert totp.consume_backup_code("WRONG-CODE1", rest, "pep") is None


def test_provisioning_uri():
    uri = totp.provisioning_uri("ABCDEF", "dr.amina", "MediCore HMIS")
    assert uri.startswith("otpauth://totp/MediCore%20HMIS%3Adr.amina?") and "secret=ABCDEF" in uri


def _integrity():
    # integrity imports app.security.crypto; alias our already-loaded module so no Flask import happens
    import sys, types
    pkg = types.ModuleType("app"); sec = types.ModuleType("app.security")
    sec.crypto = crypto; pkg.security = sec
    sys.modules.setdefault("app", pkg); sys.modules["app.security"] = sec; sys.modules["app.security.crypto"] = crypto
    return _load("integrity")


def test_audit_chain_detects_edit_delete_and_passes_clean():
    import datetime
    integ = _integrity()
    key = b"k" * 32
    rows, prev = [], None
    for i in range(1, 6):
        r = dict(id=i, user_id=1, hospital_id=1, action="view", model_name="Patient", record_id=i,
                 details="{}", ip_address="1.1.1.1", timestamp=datetime.datetime(2026, 9, 28, 10, 0, i),
                 patient_id=i, outcome="success", request_path="/p", prev_hash=prev)
        r["entry_hash"] = integ.seal(integ.audit_fields(r), prev, key)
        prev = r["entry_hash"]; rows.append(r)
    assert integ.verify_chain(rows, key)["ok"]
    edited = [dict(r) for r in rows]; edited[2]["details"] = '{"x":1}'
    res = integ.verify_chain(edited, key)
    assert res["tampered"] == [3] and not res["ok"]
    gap = [r for r in rows if r["id"] != 3]          # a middle row removed
    assert integ.verify_chain(gap, key)["broken_links"] == [4]
    legacy = [dict(id=0, entry_hash=None)] + rows
    assert integ.verify_chain(legacy, key)["legacy"] == 1


def test_oidc_pure_parts():
    oidc = _load("oidc")
    v, c = oidc.pkce_pair()
    import hashlib, base64 as b64
    assert c == b64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    cfg = {"DHA_OIDC_CLIENT_ID": "cid", "DHA_OIDC_REDIRECT_URI": "https://x/cb", "DHA_OIDC_SCOPES": "openid email"}
    url = oidc.build_auth_url(cfg, {"authorization_endpoint": "https://idp/auth"}, "st", "no", c)
    assert url.startswith("https://idp/auth?") and "code_challenge_method=S256" in url and "state=st" in url
    ok = {"iss": "I", "aud": "cid", "nonce": "n", "exp": 2000, "sub": "u1"}
    assert oidc.validate_claims(ok, "I", "cid", "n", now=1000)["sub"] == "u1"
    for bad in ({**ok, "iss": "X"}, {**ok, "aud": "other"}, {**ok, "nonce": "z"}, {**ok, "exp": 10}, {**ok, "sub": ""}):
        with pytest.raises(oidc.OidcError):
            oidc.validate_claims(bad, "I", "cid", "n", now=1000)
    assert oidc.validate_claims({**ok, "aud": ["a", "cid"]}, "I", "cid", "n", now=1000)
    assert not oidc.is_configured({}) and oidc.is_configured({k: "x" for k in (
        "DHA_OIDC_ISSUER", "DHA_OIDC_CLIENT_ID", "DHA_OIDC_CLIENT_SECRET", "DHA_OIDC_REDIRECT_URI")})
