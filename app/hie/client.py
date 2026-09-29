"""HTTP client for the Kenya HIE. Modes: off (refuse), mock (build+store, do not
send), live (POST). `transport` can be injected for tests."""
import json
import time


class HieError(Exception):
    pass


def _default_transport():
    import requests
    return requests


class HieClient:
    def __init__(self, cfg, transport=None):
        self.cfg = cfg
        self.mode = (cfg.get("HIE_MODE") or "off").lower()
        self.base = (cfg.get("HIE_BASE_URL") or "").rstrip("/")
        self._t = transport
        self._token, self._token_exp = None, 0

    @property
    def transport(self):
        return self._t or _default_transport()

    def configured(self):
        return self.mode in ("mock", "live") and (self.mode == "mock" or bool(self.base))

    def _headers(self):
        h = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
        kind = (self.cfg.get("HIE_AUTH_TYPE") or "bearer").lower()
        if kind == "bearer" and self.cfg.get("HIE_API_TOKEN"):
            h["Authorization"] = "Bearer " + self.cfg["HIE_API_TOKEN"]
        elif kind == "oauth2":
            h["Authorization"] = "Bearer " + self._oauth_token()
        return h

    def _auth(self):
        if (self.cfg.get("HIE_AUTH_TYPE") or "").lower() == "basic":
            return (self.cfg.get("HIE_USERNAME", ""), self.cfg.get("HIE_PASSWORD", ""))
        return None

    def _oauth_token(self):
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        r = self.transport.post(self.cfg["HIE_TOKEN_URL"], data={
            "grant_type": "client_credentials", "client_id": self.cfg.get("HIE_CLIENT_ID"),
            "client_secret": self.cfg.get("HIE_CLIENT_SECRET")}, timeout=self.cfg.get("HIE_TIMEOUT_SECONDS", 15))
        if r.status_code >= 400:
            raise HieError(f"Token request failed ({r.status_code}).")
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 300))
        return self._token

    def send_bundle(self, bundle):
        """Returns (status, http_status, excerpt). status: Sent / Mock / Failed."""
        if self.mode == "off":
            raise HieError("HIE_MODE is off; nothing was sent.")
        if self.mode == "mock":
            return "Mock", None, "Mock mode: message built and stored, not sent."
        if not self.base:
            raise HieError("HIE_BASE_URL is not set.")
        url = self.base + (self.cfg.get("HIE_BUNDLE_PATH") or "/Bundle")
        try:
            r = self.transport.post(url, data=json.dumps(bundle), headers=self._headers(), auth=self._auth(),
                                    timeout=self.cfg.get("HIE_TIMEOUT_SECONDS", 15))
        except Exception as e:  # network errors
            return "Failed", None, f"{e.__class__.__name__}: {e}"[:900]
        ok = 200 <= r.status_code < 300
        return ("Sent" if ok else "Failed"), r.status_code, (r.text or "")[:900]

    def pull(self, system, value):
        if self.mode != "live":
            raise HieError("Pulling records needs HIE_MODE=live.")
        path = (self.cfg.get("HIE_PULL_PATH") or "").format(system=system, value=value)
        r = self.transport.get(self.base + path, headers=self._headers(), auth=self._auth(),
                               timeout=self.cfg.get("HIE_TIMEOUT_SECONDS", 15))
        if r.status_code >= 400:
            raise HieError(f"HIE returned {r.status_code}.")
        return r.json()
