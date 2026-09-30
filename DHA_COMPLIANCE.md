# Kenya DHA compliance — v18

This release maps every line of the DHA attestation form (the screenshots) to code. The **/compliance** page shows, live, which
items are *Ready*, *Partial* (built but needs configuration/credentials/data), *Action* (not satisfied yet) or *Manual*
(only a person can confirm). **Tick an item on the DHA form only when this page shows it Ready, or you can honestly stand
behind it.**

## Upgrade an existing database (take a copy first)

```bash
pip install -r requirements.txt
python -c "import os,base64;print('k1:'+base64.urlsafe_b64encode(os.urandom(32)).decode())"   # put in .env as DATA_ENCRYPTION_KEYS=
python upgrade_v18.py                 # new tables, new columns, append-only triggers, roles, reference lists
python encrypt_existing_data.py       # encrypts existing national IDs, fills blind indexes
flask dha-seed
```

Keep `DATA_ENCRYPTION_KEYS` **outside the database and outside the backups** (password manager, second location).
Lose it and encrypted IDs and backups cannot be read.

Schedule on PythonAnywhere (Tasks tab): `flask backup-run` daily, `flask reporting-weekly` weekly (Monday morning),
`flask notifications-retry` hourly, `flask verify-integrity` weekly. Then confirm the two "job scheduled" items on /compliance.

## What each form section maps to

| DHA section | Where it lives |
|---|---|
| Demographics | Registration form: ID type, National ID / passport / birth certificate (encrypted + blind-indexed), Client Registry ID, county/sub-county/ward/village, email, next of kin |
| CPOE | Medication, lab, radiology (existing); **allied-health referrals** (physio, OT, nutrition, social work, counselling) with worklist, SOAP session notes, optional billing fee and confidentiality; family history; vitals; BMI/growth; MCH (ANC, PNC, child welfare, FP, delivery); immunization with routine-schedule prompts |
| Problem list (KNHTS) | `/patients/<id>/record` — persistent list, auto-fed from finalised consultations, status changes, full version history |
| Medication & HPT | Medication list fed from prescriptions (auto-stop on cancel), history, structured allergy list linked to formulary/HPT codes, HPT search + import (Terminology page) |
| Clinical decision support | `app/cds/rules.py`: allergy exact/cross-group, duplicate therapy, pregnancy caution, minimum age, critical/abnormal labs, abnormal vitals, chronic-problem prompts. Blocks a prescription until the prescriber gives a reason; every alert and override is logged. Per-rule switches |
| Clinical summary | `/patients/<id>/summary` (printable) and `.fhir.json` (FHIR R4 document bundle) |
| E-prescribing | Transmitted to the pharmacy worklist always; to the HIE too when enabled and the patient consented |
| Quality measures | `/quality`: 5 built-in measures calculated from data, manual capture, CSV import/export, submission |
| Immediate diseases / IDSR weekly / public health events | `/surveillance`: automatic notifications on finalising a matching diagnosis, 24-hour overdue flag, weekly aggregate (JSON + SDMX-ML), event log with auto cluster signal |
| ODPC / DPIA | `/security/data-protection` register (records reference numbers and where the evidence is kept) |
| Encryption | AES-256-GCM on identifiers and backups with key rotation; TLS via `FORCE_HTTPS` |
| Authentication | TOTP MFA (+ backup codes, replay protection) enforced for privileged roles or everyone; Digital Health ID via OpenID Connect + PKCE; idle logoff; RBAC |
| Emergency access | Break-glass: reason, read-only, time-limited, audited, manager review queue |
| Audit trail | HMAC hash chain (tamper evidence), field-level record history, amendment reasons, retention setting, `flask verify-integrity` |
| Backup & DR | Encrypted, checksummed, restore-tested on every run; off-site folder or S3; DR plan, RTO/RPO and drill date recorded |
| HIE / standards | FHIR R4 bundles built + structurally validated, consent-gated ('hie'), inbound HMAC-signed endpoint with clinician review; SDMX-ML export; ICD-10 base with ICD-11/SNOMED/LOINC/HPT/ATC mapping fields and CSV importers |

## DHA registry, eligibility and OTP (`app/hie/dha_client.py`)

Checked against DHA's published docs (hie-docs.dha.go.ke) on 30 Sep 2026. Patient page → **DHA eligibility** (`dha.lookup` permission).
Order follows DHA's guide: find in registry → **eligibility (decision gate)** → benefits → sub-benefits → interventions, then OTP consent.

**Confirmed in the docs and now implemented**
* Sub-benefits need a `parent_benefit_code` (read from the benefits step); the old client omitted it and skipped the benefits call.
* Send OTP requires `intervention_codes`; the old client didn't send them. DHA returns a `consent_request_id`.
* Multi-facility credentials (MediCore is multi-hospital) must send `X-Facility-Id` + `X-Facility-Id-Type: fr-code` on every request. New per-hospital field
  **Admin → Settings → DHA Facility Registry code** (looks like `FID-47-115307-8`; not the same as the numeric MFL code).
* `identification_type` spellings differ: Patient Search uses `NATIONAL ID`, `BIRTH CERTIFICATE NUMBER`, `ALIEN ID`, `REFUGEE ID`, `CR ID`; Eligibility uses
  `National ID`, `Birth Certificate`, `Alien ID`, `Refugee ID`, `ClientRegistry ID`. Each endpoint now has its own map (override with `HIE_SEARCH_ID_TYPE_MAP` / `HIE_ELIGIBILITY_ID_TYPE_MAP`).
* **Passport is not accepted** by either endpoint, so it is no longer offered; foreign visitors need another route.
* DHA recommends the Client Registry ID for eligibility, so once saved it is used first.
* Eligibility flags are honoured: `isAlive` (deceased needs DHA's deceased workflow), `whitelistedForOTP`, `facilityBiometricsEnforced` (OTP is disabled when required).
  POMSF/TSC/USALAMA membership is detected by scheme-name **prefix**, as DHA instructs.

**Still unverified (their API reference pages are JavaScript-rendered) — confirm in UAT**
* Base URL; token endpoint, request body and response fields (the client uses `POST /api/v1/tenants/token` with JSON `client_id`/`client_secret`);
  the send-OTP path (`/api/v1/claims/otp`); whether `patients/eligibility` and `patients/benefits` carry the `/api/v1` prefix; the patient-search path;
  and the exact response shapes (the screen shows raw JSON and suggests the CR id, which you confirm before saving).
* Older DHA material (a 2025 HMIS guide) listed patient search by ID number plus full name and date of birth; the current guide needs only type and number.
  If UAT asks for more, add it in `search_patient`.

Every call still needs the patient's `sha_claims` consent and is audited (who, whom, step, ok/failed; never the identifier or response).

## Honest limits — read before you tick anything

* **Nothing here has been tested against DHA's live systems.** The HIE, HPT registry, surveillance endpoint and Digital Health ID
  client are complete but need DHA-issued URLs and credentials, and their real API contracts may differ from the configurable paths
  (`HIE_BUNDLE_PATH`, etc.). Use `HIE_MODE=mock` to rehearse. "Certified" is DHA's decision, not ours.
* **Terminology is not pre-filled.** ICD-11, SNOMED CT, LOINC and HPT codes must be imported from the official mapping tables;
  WHO growth-standard tables must be loaded with `import_growth_reference.py`. Until then those items stay *Partial*.
* **The notifiable-disease list and decision-support rules are starter content.** Have the MOH circular and your clinical lead
  review them; the dashboard keeps those items *Partial* until someone confirms.
* **No drug–drug interaction database** is included. That needs a licensed dataset.
* **Encryption covers identifiers and backups, not the whole database file.** Use disk/volume encryption for full at-rest coverage.
  The key ring is application-level, not an HSM/cloud KMS (the form asks about a "Key Management System": answer accordingly).
* **Audit tamper-evidence** proves alterations to anyone without the audit key. Someone controlling both the database *and* the app
  environment could re-seal a rewritten chain; keep `AUDIT_HMAC_KEY` off the database host and note the latest hash externally.
* **SDMX-ML output is a simplified generic-data message**; confirm the data-structure ids DHA expects.
* The sandbox that produced this release could not install Flask/SQLAlchemy, so the database-backed code was checked by static
  analysis (compilation, endpoint and undefined-name cross-checks, template parsing) and the pure logic (crypto, MFA, audit chain, CDS, FHIR, surveillance, backups) by 47 unit tests, but
  `tests/test_dha_integration.py` **has not been run**. Run the full suite before deploying.
