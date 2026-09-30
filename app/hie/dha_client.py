"""Client for the DHA Health Information Exchange REST APIs (hie-docs.dha.go.ke).

Checked against hie-docs.dha.go.ke on 30 Sep 2026 (see VERIFIED / STILL UNVERIFIED below).
Each unverified item is isolated in one place so it is a one-line fix once you
have your UAT credentials.

VERIFIED in DHA's published docs
  * GET  /api/v1/patients/contacts?patient_id=...            (contacts guide)
  * POST /api/v1/claims/authorize                            (biometrics path; not used here)
  * eligibility flow: GET /patients/eligibility (identification_type,
    identification_number query params) -> benefits (patient_id) ->
    sub-benefits (patient_id, parent_benefit_code) -> interventions (patient_id,
    sub_benefit_code). patient_id is the Client Registry (CR) ID.
  * Send OTP takes patient_id, intervention_codes (required list), contact_id (optional)
  * Facility scoping: a token with no facility claim (multi-facility HMIS such as
    MediCore) MUST send X-Facility-Id (facility registry code, e.g. FID-47-115307-8)
    and X-Facility-Id-Type: fr-code on every request.
  * identification_type values differ by endpoint (see app/hie/service.py maps).

STILL UNVERIFIED (their API reference pages are JavaScript-rendered)
  * base URL, the token endpoint path/body/response, the send-OTP path, the exact
    /api/v1 prefix on patients/eligibility and patients/benefits, patient-search path.

Uses existing config: HIE_MODE, HIE_BASE_URL, HIE_CLIENT_ID, HIE_CLIENT_SECRET,
HIE_TIMEOUT_SECONDS. Optional: HIE_TOKEN_URL (defaults to base + TOKEN_PATH).
"""
import time

from app.hie.client import HieError

TOKEN_PATH = "/api/v1/tenants/token"
PATIENTS = "/api/v1/patients"
ELIGIBILITY = "/api/v1/patients/eligibility"
SUB_BENEFITS = "/api/v1/patients/sub-benefits"
BENEFITS = "/api/v1/patients/benefits"
INTERVENTIONS = "/api/v1/patients/benefits/interventions"
CONTACTS = "/api/v1/patients/contacts"
SEND_OTP = "/api/v1/claims/otp"


def _default_transport():
    import requests
    return requests


class DhaHieClient:
    def __init__(self, cfg, transport=None, facility_id=None, _state=None):
        self.cfg = cfg
        self.mode = (cfg.get("HIE_MODE") or "off").lower()
        self.base = (cfg.get("HIE_BASE_URL") or "").rstrip("/")
        self._t = transport
        self.facility_id = (facility_id or "").strip() or None   # facility registry code, e.g. FID-47-115307-8
        self._state = _state if _state is not None else {"token": None, "exp": 0}  # shared by for_facility() copies

    def for_facility(self, facility_id):
        """Same credentials and cached token, scoped to one facility for every request."""
        return DhaHieClient(self.cfg, self._t, facility_id, self._state)

    @property
    def transport(self):
        return self._t or _default_transport()

    def _check(self):
        if self.mode != "live":
            raise HieError("DHA lookups need HIE_MODE=live (point HIE_BASE_URL at DHA's UAT URL to test).")
        if not (self.base and self.cfg.get("HIE_CLIENT_ID") and self.cfg.get("HIE_CLIENT_SECRET")):
            raise HieError("Set HIE_BASE_URL, HIE_CLIENT_ID and HIE_CLIENT_SECRET.")

    def _timeout(self):
        return self.cfg.get("HIE_TIMEOUT_SECONDS", 15)

    # --- auth: POST /api/v1/tenants/token {client_id, client_secret} -> access_token
    def token(self, force=False):
        self._check()
        if not force and self._state["token"] and time.time() < self._state["exp"] - 30:
            return self._state["token"]
        url = self.cfg.get("HIE_TOKEN_URL") or (self.base + TOKEN_PATH)
        r = self.transport.post(url, json={"client_id": self.cfg["HIE_CLIENT_ID"],
                                           "client_secret": self.cfg["HIE_CLIENT_SECRET"]},
                                timeout=self._timeout())
        if r.status_code >= 400:
            raise HieError(f"DHA token request failed ({r.status_code}).")
        data = r.json()
        tok = data.get("access_token")
        if not tok:
            raise HieError("DHA token response had no access_token.")
        self._state["token"], self._state["exp"] = tok, time.time() + int(data.get("expires_in", 300))
        return tok

    def _call(self, method, path, params=None, body=None, _retry=True):
        self._check()
        headers = {"Authorization": "Bearer " + self.token(), "Accept": "application/json"}
        if self.facility_id:  # both headers are required together (DHA: Facility Identification)
            headers["X-Facility-Id"] = self.facility_id
            headers["X-Facility-Id-Type"] = "fr-code"
        fn = getattr(self.transport, method)
        kw = {"headers": headers, "timeout": self._timeout()}
        if params:
            kw["params"] = params
        if body is not None:
            kw["json"] = body
        r = fn(self.base + path, **kw)
        if r.status_code == 401 and _retry:  # expired/revoked token: refresh once
            self.token(force=True)
            return self._call(method, path, params, body, _retry=False)
        if r.status_code >= 400:
            # never echo the full body: it can contain patient data
            raise HieError(f"DHA returned {r.status_code} for {path}: {(r.text or '')[:200]}")
        return r.json()

    # --- registries / eligibility (the 4 prerequisite steps of every visit)
    def search_patient(self, identification_number, identification_type):
        return self._call("get", PATIENTS, {"identification_number": identification_number,
                                            "identification_type": identification_type})

    def eligibility(self, identification_number, identification_type):
        return self._call("get", ELIGIBILITY, {"identification_number": identification_number,
                                               "identification_type": identification_type})

    def benefits(self, patient_id):
        """Benefit packages (parent benefits). Read parentBenefitCode off each result."""
        return self._call("get", BENEFITS, {"patient_id": patient_id})

    def sub_benefits(self, patient_id, parent_benefit_code):
        return self._call("get", SUB_BENEFITS, {"patient_id": patient_id,
                                                "parent_benefit_code": parent_benefit_code})

    def interventions(self, patient_id, sub_benefit_code):
        return self._call("get", INTERVENTIONS, {"patient_id": patient_id,
                                                 "sub_benefit_code": sub_benefit_code})

    # --- consent (OTP): masked contacts -> send OTP
    def otp_contacts(self, patient_id):
        return self._call("get", CONTACTS, {"patient_id": patient_id})

    def send_otp(self, patient_id, intervention_codes, contact_id=None):
        """intervention_codes (required): the services the patient is consenting to. If contact_id
        is omitted DHA sends to the patient's default contact. Returns a consent_request_id."""
        codes = [c for c in (intervention_codes or []) if c]
        if not codes:
            raise HieError("Choose at least one intervention code the patient is consenting to.")
        body = {"patient_id": patient_id, "intervention_codes": codes}
        if contact_id:
            body["contact_id"] = contact_id
        return self._call("post", SEND_OTP, body=body)


# ---------------------------------------------------------------------------
# Reading DHA responses (field names from the Eligibility Check guide)
# ---------------------------------------------------------------------------
def _flat(data):
    """The eligibility payload may be wrapped ({"data": {...}}); return the inner dict."""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
        return data
    return {}


def eligibility_flags(data):
    """isAlive / whitelistedForOTP / facilityBiometricsEnforced (None when absent) plus scheme names."""
    d = _flat(data)
    schemes = d.get("schemes") or []
    names = [str(x.get("schemeName")) for x in schemes if isinstance(x, dict) and x.get("schemeName")]
    return {"is_alive": d.get("isAlive"), "whitelisted_for_otp": d.get("whitelistedForOTP"),
            "biometrics_enforced": d.get("facilityBiometricsEnforced"), "schemes": names,
            "cr_id": d.get("memberCrNumber"), "full_name": d.get("fullName")}


def is_pomsf(scheme_names):
    """DHA: match POMSF as a PREFIX (POMSF-047, POMSF-SHA...), never by equality; also TSC and USALAMA."""
    return any(n.upper().startswith(("POMSF", "TSC", "USALAMA")) for n in scheme_names)


def otp_allowed(flags):
    """(allowed, reason). OTP consent is unavailable when the patient isn't whitelisted or the facility
    must use biometrics; a deceased patient needs the deceased-patient workflow instead."""
    if flags.get("is_alive") is False:
        return False, "DHA records this patient as deceased. Use DHA's deceased-patient workflow."
    if flags.get("biometrics_enforced") is True:
        return False, "This facility must use biometrics for consent; OTP is not available."
    if flags.get("whitelisted_for_otp") is False:
        return False, "This patient is not whitelisted for OTP consent."
    return True, None
