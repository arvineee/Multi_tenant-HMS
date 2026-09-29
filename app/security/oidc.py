"""Digital Health ID sign-in (OpenID Connect authorization-code flow with PKCE).

Needs DHA-issued client credentials (DHA_OIDC_*). Nothing here can be
exercised end-to-end without them; the pure parts (PKCE, URL building, claim
validation) are unit-tested. ID tokens are verified against the issuer's JWKS
with PyJWT.
"""
import base64
import hashlib
import secrets
import time
import urllib.parse


class OidcError(Exception):
    pass


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def is_configured(cfg):
    return all(cfg.get(k) for k in ("DHA_OIDC_ISSUER", "DHA_OIDC_CLIENT_ID", "DHA_OIDC_CLIENT_SECRET", "DHA_OIDC_REDIRECT_URI"))


def discover(cfg, http):
    r = http.get(cfg["DHA_OIDC_ISSUER"] + "/.well-known/openid-configuration", timeout=10)
    if r.status_code != 200:
        raise OidcError("Could not reach the Digital Health ID service.")
    doc = r.json()
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if k not in doc:
            raise OidcError(f"Discovery document is missing {k}.")
    return doc


def build_auth_url(cfg, doc, state, nonce, challenge):
    q = {"response_type": "code", "client_id": cfg["DHA_OIDC_CLIENT_ID"], "redirect_uri": cfg["DHA_OIDC_REDIRECT_URI"],
         "scope": cfg.get("DHA_OIDC_SCOPES") or "openid", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return doc["authorization_endpoint"] + "?" + urllib.parse.urlencode(q)


def exchange_code(cfg, doc, code, verifier, http):
    r = http.post(doc["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": cfg["DHA_OIDC_REDIRECT_URI"],
        "client_id": cfg["DHA_OIDC_CLIENT_ID"], "client_secret": cfg["DHA_OIDC_CLIENT_SECRET"],
        "code_verifier": verifier}, timeout=15)
    if r.status_code != 200:
        raise OidcError("The Digital Health ID service refused the sign-in.")
    tok = r.json()
    if "id_token" not in tok:
        raise OidcError("No ID token was returned.")
    return tok


def validate_claims(claims, issuer, client_id, nonce, now=None, leeway=60):
    now = now or time.time()
    if claims.get("iss") != issuer:
        raise OidcError("Token issuer mismatch.")
    aud = claims.get("aud")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OidcError("Token audience mismatch.")
    if claims.get("nonce") != nonce:
        raise OidcError("Token nonce mismatch.")
    if not claims.get("exp") or claims["exp"] + leeway < now:
        raise OidcError("Token has expired.")
    if not claims.get("sub"):
        raise OidcError("Token has no subject.")
    return claims


def verify_id_token(id_token, cfg, doc, nonce):
    import jwt  # PyJWT
    client = jwt.PyJWKClient(doc["jwks_uri"])
    key = client.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256", "ES256"], audience=cfg["DHA_OIDC_CLIENT_ID"],
                        issuer=doc["issuer"], options={"require": ["exp", "iss", "aud", "sub"]})
    return validate_claims(claims, doc["issuer"], cfg["DHA_OIDC_CLIENT_ID"], nonce)
