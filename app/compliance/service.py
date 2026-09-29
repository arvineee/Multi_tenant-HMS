"""Compliance dashboard: maps the DHA attestation form (the screens you shared)
to what MediCore can verify on its own, what depends on configuration or
credentials, and what only a person can confirm.

Status meanings
  ready    - built and (where possible) verified by this system: tick it on the DHA form
  partial  - built, but needs configuration/credentials/data before you can honestly tick it
  action   - not satisfied yet; the detail says what to do
  manual   - the software cannot verify it; a named person attests below
"""
import datetime

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import Role, User, AuditLog
from app.security import crypto, integrity
from app.security.models import (UserMfa, DataProtectionRegistration, DpiaRecord, ComplianceAttestation)
from app.records import service as rec
from app.terminology import service as term
from app.backup import service as backup_service

READY, PARTIAL, ACTION, MANUAL = "ready", "partial", "action", "manual"

ATTESTATIONS = {
    "disease_list_reviewed": "Notifiable disease list has been reviewed against the current MOH IDSR circular",
    "weekly_job_scheduled": "The weekly IDSR job (flask reporting-weekly) is scheduled on the server",
    "backup_job_scheduled": "The backup job (flask backup-run) is scheduled on the server",
    "tls_enforced": "The site is served only over HTTPS/TLS 1.2+ (hosting/proxy setting)",
    "external_kms": "Encryption keys are held in an external KMS/HSM (leave unticked if the key ring in .env is used)",
    "cds_clinically_reviewed": "The decision-support rules have been reviewed by your clinical lead",
    "hie_certified": "The system has passed DHA HIE certification / connectivity testing",
}


def _att(org_id):
    return {a.item_key: a for a in ComplianceAttestation.query.filter_by(organization_id=org_id).all()}


def _i(key, label, status, detail="", link=None):
    return {"key": key, "label": label, "status": status, "detail": detail, "link": link}


def _plain_identifier_count():
    n = 0
    for col in ("national_id", "passport_number", "birth_certificate_number"):
        n += db.session.execute(text(
            f"SELECT COUNT(*) FROM patients WHERE {col} IS NOT NULL AND {col} <> '' AND {col} NOT LIKE 'enc:v1:%'")).scalar() or 0
    return n


def _audit_check():
    rows = [dict(id=r.id, user_id=r.user_id, hospital_id=r.hospital_id, action=r.action, model_name=r.model_name,
                 record_id=r.record_id, details=r.details, ip_address=r.ip_address, timestamp=r.timestamp,
                 patient_id=r.patient_id, outcome=r.outcome, request_path=r.request_path, prev_hash=r.prev_hash,
                 entry_hash=r.entry_hash) for r in AuditLog.query.order_by(AuditLog.id).all()]
    return integrity.verify_chain(rows)


def evaluate(org, is_secure_request=False):
    cfg = current_app.config
    att = _att(org.id)
    a = lambda k: bool(att.get(k) and att[k].value)  # noqa: E731
    cov = term.coverage(org.id)
    icd11 = cov["ICD-11"][0] or 0
    sections = []

    # ---- Demographics ---------------------------------------------------
    sections.append(("Demographics & Patient Registration", [
        _i("demo_sex", "Captures Sex/Gender", READY), _i("demo_dob", "Captures Date of Birth (or estimated age)", READY),
        _i("demo_addr", "Captures Residence/Address (county, sub-county, ward, village)", READY),
        _i("demo_contact", "Captures Contact Information (phone, email)", READY),
        _i("demo_nok", "Captures Next of Kin", READY),
        _i("demo_nid", "Captures National ID (encrypted, blind-indexed)", READY),
        _i("demo_pp", "Captures Passport Number (encrypted)", READY),
        _i("demo_bc", "Captures Birth Certificate (encrypted)", READY),
        _i("demo_knhts", "KNHTS compliant", READY if icd11 >= 95 else PARTIAL,
           f"ICD-11 mapping coverage {icd11}% of the diagnosis catalog. Import the KNHTS/WHO ICD-10 to ICD-11 map on the Terminology page.",
           "terminology.index"),
    ]))
    growth_loaded = rec.reference_loaded()
    sections.append(("Computerized Provider Order Entry (CPOE)", [
        _i("cpoe_med", "Supports Medications", READY), _i("cpoe_disp", "Supports Dispensing", READY),
        _i("cpoe_lab", "Supports Laboratory Orders", READY), _i("cpoe_rad", "Supports Radiology Orders", READY),
        _i("cpoe_physio", "Supports Physiotherapy", READY), _i("cpoe_ot", "Supports Occupation Therapy", READY),
        _i("cpoe_nutr", "Supports Nutrition/Dietetics", READY), _i("cpoe_sw", "Supports Social Work", READY),
        _i("cpoe_couns", "Supports Counselling", READY), _i("cpoe_fh", "Supports Family History", READY),
        _i("cpoe_vit", "Supports Vital Signs", READY),
        _i("cpoe_growth", "Supports BMI/Growth Charts", READY if growth_loaded else PARTIAL,
           "BMI works now. WHO z-scores need the WHO reference tables imported (import_growth_reference.py)." if not growth_loaded else "WHO reference loaded."),
        _i("cpoe_bill", "Supports Billing", READY), _i("cpoe_mch", "Supports MCH Encounter", READY),
    ]))
    sections.append(("Problem List & Diagnoses (KNHTS)", [
        _i("pl_knhts", "KNHTS Compliant", READY if icd11 >= 95 else PARTIAL, "Problems carry ICD-10, ICD-11 and SNOMED CT codes from the catalog."),
        _i("pl_rec", "Can Record Problems", READY), _i("pl_upd", "Can Update Problems", READY),
        _i("pl_hist", "Can Access Problem History", READY, "Field-level version history with amendment reasons."),
    ]))
    hpt_cov = cov["HPT (drugs)"][0] or 0
    sections.append(("Medication Management & HPT Registry", [
        _i("mm_hpt", "HPT Registry Integration", READY if (cfg.get("HPT_REGISTRY_URL") and hpt_cov > 0) else PARTIAL,
           f"HPT code coverage on the formulary: {hpt_cov}%. Set HPT_REGISTRY_URL or import codes on the Terminology page.", "terminology.index"),
        _i("mm_active", "Active Medication List", READY), _i("mm_hist", "Medication History", READY),
        _i("mm_al", "Allergy List", READY), _i("mm_alh", "Allergy History", READY),
        _i("mm_hptal", "HPT Allergy Integration", READY, "Allergies can link to a formulary drug and carry an HPT code."),
    ]))
    reviewed = a("cds_clinically_reviewed")
    sections.append(("Clinical Decision Support", [
        _i("cds_ebi", "Evidence-Based Interventions", READY if reviewed else PARTIAL,
           "Starter rules are built in; your clinical lead must review them (attest below)." if not reviewed else "Reviewed."),
        _i("cds_pl", "Uses Problem List", READY), _i("cds_hpt", "Uses HPT Registry", READY),
        _i("cds_al", "Uses Allergy List", READY), _i("cds_demo", "Uses Demographics", READY, "Age and sex drive pediatric and pregnancy checks."),
        _i("cds_lab", "Uses Lab Results", READY, "Critical/abnormal flags need reference ranges on the lab catalog."),
        _i("cds_vit", "Uses Vital Signs", READY),
    ]))
    hie_live = cfg.get("HIE_MODE") == "live"
    sections.append(("Clinical Summary Generation", [
        _i("cs_hr", "Human-Readable Format", READY), 
        _i("cs_hie", "Kenya HIE Exchangeable", READY if hie_live else PARTIAL, "FHIR R4 document bundle built and validated. Needs HIE_MODE=live and DHA credentials to actually exchange."),
        _i("cs_bio", "Includes Biodata", READY), _i("cs_clin", "Includes Clinical Information", READY),
        _i("cs_med", "Includes Medications", READY), _i("cs_rx", "Includes Prescriptions", READY),
        _i("cs_cp", "Includes Care Plan", READY),
    ]))
    sections.append(("Electronic Prescribing", [
        _i("ep_create", "Can Create Prescriptions", READY),
        _i("ep_tx", "Electronic Transmission", READY if hie_live else PARTIAL, "Reaches the pharmacy worklist electronically; HIE transmission needs HIE_MODE=live."),
        _i("ep_dx", "Includes Diagnostic Tests", READY), _i("ep_pl", "Includes Problem List", READY),
        _i("ep_ml", "Includes Medication Lists", READY),
    ]))
    surv_live = cfg.get("SURVEILLANCE_MODE") == "live"
    sections.append(("Quality Measures & Reporting", [
        _i("qm_cap", "Can Capture Quality Measures", READY), _i("qm_calc", "Can Calculate Quality Measures", READY),
        _i("qm_imp", "Can Import Quality Measures", READY), _i("qm_exp", "Can Export Quality Measures", READY),
        _i("qm_sub", "Electronic Submission", READY if surv_live else PARTIAL, "Needs SURVEILLANCE_MODE=live and the receiving endpoint."),
    ]))
    sections.append(("Reporting Capabilities", [
        _i("rp_imm_rt", "Immediate Reportable Diseases: Real-Time Reporting", READY if surv_live else PARTIAL,
           "Notifications are created the moment a consultation is finalised; transmission needs SURVEILLANCE_MODE=live."),
        _i("rp_imm_moh", "Immediate Reportable Diseases: MOH Guidelines Compliant", READY if a("disease_list_reviewed") else PARTIAL,
           "Starter list included; confirm against the current MOH circular (attest below)."),
        _i("rp_idsr_auto", "IDSR Weekly Reporting: Automated Reports", READY if a("weekly_job_scheduled") else PARTIAL,
           "Run `flask reporting-weekly` weekly (cron / PythonAnywhere scheduled task) and attest below."),
        _i("rp_idsr_sub", "IDSR Weekly Reporting: Weekly Submission", READY if surv_live else PARTIAL),
        _i("rp_phe", "Public Health Events", READY),
    ]))

    # ---- Security & privacy ---------------------------------------------
    reg = {(r.organization_id, r.role): r for r in DataProtectionRegistration.query.filter(
        db.or_(DataProtectionRegistration.organization_id == org.id, DataProtectionRegistration.organization_id.is_(None))).all()}
    ctrl, proc = reg.get((org.id, "controller")), reg.get((None, "processor")) or reg.get((org.id, "processor"))
    dp = lambda r: (READY if r and r.is_current else ACTION)  # noqa: E731
    dpia = DpiaRecord.query.filter(db.or_(DpiaRecord.organization_id == org.id, DpiaRecord.organization_id.is_(None))).order_by(DpiaRecord.completed_on.desc()).first()
    sections.append(("Data Protection (ODPC & DPIA)", [
        _i("dp_odpc", "Registered with ODPC", dp(ctrl) if ctrl or proc else ACTION, "Record your registration certificate.", "security.data_protection"),
        _i("dp_ctrl", "Data Controller Registered", dp(ctrl), "Your organization's registration (Kenya Data Protection Act, 2019).", "security.data_protection"),
        _i("dp_proc", "Data Processor Registered", dp(proc), "MediCore's registration as processor, recorded by the platform operator.", "security.data_protection"),
        _i("dp_dpia", "DPIA Completed", READY if dpia and dpia.is_current else ACTION, "Record the assessment and next review date.", "security.data_protection"),
    ]))
    plain = _plain_identifier_count()
    ring = crypto.get_keyring()
    enc_status = ACTION if plain else (PARTIAL if ring.derived else READY)
    tls_ok = bool(cfg.get("FORCE_HTTPS") or cfg.get("SESSION_COOKIE_SECURE")) and (is_secure_request or a("tls_enforced"))
    sections.append(("Encryption", [
        _i("enc_rest", "Data at Rest Encrypted", enc_status,
           (f"{plain} identifier value(s) are still plain text: run `python encrypt_existing_data.py`." if plain else
            ("Encrypting, but with a key derived from SECRET_KEY. Set DATA_ENCRYPTION_KEYS." if ring.derived else "Identifiers AES-256-GCM encrypted; backups encrypted."))
           + " Note: this covers identifiers and backups, not the whole database file; use an encrypted disk/volume for full-disk encryption."),
        _i("enc_transit", "Data in Transit Encrypted", READY if tls_ok else PARTIAL, "Set FORCE_HTTPS=true and SESSION_COOKIE_SECURE=true behind HTTPS; attest TLS below."),
        _i("enc_std", "Encryption Standard", READY, "AES-256-GCM (at rest), TLS 1.2+ (transit)."),
        _i("enc_kms", "Key Management System Implemented", READY if a("external_kms") else PARTIAL,
           "An application key ring with rotation is built in; an external KMS/HSM is not. Attest only if you use one."),
    ]))
    users = User.query.filter_by(organization_id=org.id, is_active=True).all()
    need = [u for u in users if u.role and (u.role.name in cfg.get("MFA_REQUIRED_ROLES", []))]
    enrolled = {m.user_id for m in UserMfa.query.filter_by(is_enabled=True).all()}
    missing = [u for u in need if u.id not in enrolled]
    nroles = Role.query.filter(Role.scope != "platform").count()
    sections.append(("Authentication & Access Control", [
        _i("au_uid", "Unique User Identification", READY),
        _i("au_dhaid", "Digital Health ID Integration", READY if (cfg.get("DHA_OIDC_ISSUER") and cfg.get("DHA_OIDC_CLIENT_ID")) else PARTIAL,
           "Built (OpenID Connect + PKCE). Needs the client credentials DHA issues."),
        _i("au_mfa", "Multi-Factor Authentication (MFA)", READY if not missing else ACTION,
           f"{len(need) - len(missing)}/{len(need)} required accounts enrolled." if need else "TOTP available; enforced for privileged roles.", "security.mfa_setup"),
        _i("au_rbac", "Role-Based Access Control (RBAC)", READY, f"{nroles} roles with per-permission checks."),
        _i("au_lvl", "Number of Permission Levels", READY, f"{nroles} (roles) across 4 scopes: department, hospital, organization, platform."),
        _i("au_logoff", "Automatic Logoff", READY, f"Idle timeout {cfg.get('IDLE_LOGOUT_MINUTES')} min in the browser and {cfg['PERMANENT_SESSION_LIFETIME'] // 60} min server-side."),
        _i("au_em", "Emergency Access Procedures", READY, "Break-glass with reason, time limit and manager review.", "security.emergency_review"),
    ]))
    chain = _audit_check()
    sections.append(("Audit Trail", [
        _i("at_tamper", "Tamper-Resistant", READY if chain["ok"] else ACTION,
           f"Hash chain: {chain['sealed']} sealed, {chain['legacy']} pre-chain rows, {len(chain['tampered'])} altered, {len(chain['broken_links'])} broken links."),
        _i("at_users", "Tracks User Actions", READY), _i("at_data", "Tracks Data Changes", READY),
        _i("at_ver", "Version Tracking", READY), _i("at_amend", "Amendment Tracking", READY, "Edits to final records require a reason."),
        _i("at_ret", "Retention Period (years)", READY, f"{cfg.get('AUDIT_RETENTION_YEARS')} years (policy value; nothing is auto-deleted)."),
    ]))
    b = backup_service.summary()
    plan = b["plan"]
    sections.append(("Backup & Disaster Recovery", [
        _i("bk_freq", "Backup Frequency", READY if (b["age_hours"] is not None and b["age_hours"] <= 26 and a("backup_job_scheduled")) else PARTIAL,
           f"Last backup {b['age_hours']}h ago." if b["age_hours"] is not None else "No backup yet. Run `flask backup-run` and schedule it daily.", "backup.index"),
        _i("bk_off", "Off-Site Backup", READY if b["offsite_ok"] else ACTION, "Set BACKUP_OFFSITE_DIR or BACKUP_S3_BUCKET.", "backup.index"),
        _i("bk_plan", "Disaster Recovery Plan", READY if plan and plan.plan_reference else ACTION, "Record where the written plan is kept.", "backup.index"),
        _i("bk_rto", "RTO (Recovery Time Objective)", READY if plan and plan.rto_hours is not None else ACTION, "", "backup.index"),
        _i("bk_rpo", "RPO (Recovery Point Objective)", READY if plan and plan.rpo_hours is not None else ACTION, "", "backup.index"),
        _i("bk_test", "Backup/Recovery Tested", READY if (b["last_ok"] and b["last_ok"].verify_status and "passed" in b["last_ok"].verify_status) else ACTION,
           "Every backup is decrypted and opened as a restore test." if b["last_ok"] else "", "backup.index"),
        _i("bk_date", "Last Test Date", READY if (plan and plan.last_test_date) or (b["last_ok"] and b["last_ok"].verified_at) else ACTION,
           f"{(plan.last_test_date if plan and plan.last_test_date else b['last_ok'].verified_at.date()) if (plan and plan.last_test_date) or (b['last_ok'] and b['last_ok'].verified_at) else ''}"),
    ]))
    sections.append(("Data Integrity", [
        _i("di_chain", "Audit chain verified", READY if chain["ok"] else ACTION, "Run `flask verify-integrity` any time."),
        _i("di_rv", "Record history append-only", READY, "ORM guards; add DB triggers with upgrade_v18.py."),
    ]))

    # ---- Data exchange ----------------------------------------------------
    sections.append(("Kenya Health Information Exchange (HIE)", [
        _i("hie_conn", "Able to connect to Kenya HIE", READY if (hie_live and a("hie_certified")) else PARTIAL,
           "Client built. Certification needs DHA credentials and their sign-off; set HIE_MODE=live, then attest."),
        _i("hie_tx", "Can Transmit Data to HIE", READY, "Consent-gated ('hie' purpose); every attempt logged.", "hie.transactions"),
        _i("hie_rx", "Can Receive Data from HIE", READY, "Inbound endpoint /hie/inbound (HMAC-signed) with clinician review queue.", "hie.inbox"),
        _i("hie_fhir", "FHIR Compliant", READY, "FHIR R4 bundles, structurally validated. DHA's profile validator must still pass them."),
    ]))
    sections.append(("Data Exchange Standards", [
        _i("std_fhir", "FHIR Compliant", READY), _i("std_sdmx", "SDMX Compliant", READY, "SDMX-ML generic data export for weekly reports; confirm the DSD ids DHA expects."),
    ]))
    lvl4 = hie_live and cfg.get("HIE_AUTO_SEND")
    sections.append(("Interoperability Maturity Level", [
        _i("im1", "Level 1: Unstructured Data Exchange", READY, "Printable clinical summary, prescriptions, documents."),
        _i("im2", "Level 2: Structured Data Exchange", READY, "JSON/CSV/SDMX-ML exports."),
        _i("im3", "Level 3: Semantic Interoperability", READY if (icd11 >= 95 and (cov["LOINC (lab)"][0] or 0) >= 80) else PARTIAL,
           "FHIR R4 with ICD/SNOMED/LOINC. Needs terminology coverage (ICD-11 >=95%, LOINC >=80%)."),
        _i("im4", "Level 4: Automated Data Sharing", READY if lvl4 else PARTIAL, "Needs HIE_MODE=live and HIE_AUTO_SEND=true."),
    ]))
    sections.append(("Medical Terminology Standards", [
        _i("tm_snomed", "SNOMED CT", READY if (cov["SNOMED CT (diagnosis)"][0] or 0) >= 50 else PARTIAL, f"Diagnosis coverage {cov['SNOMED CT (diagnosis)'][0] or 0}%."),
        _i("tm_icd11", "ICD-11", READY if icd11 >= 95 else PARTIAL, f"Coverage {icd11}%."),
        _i("tm_loinc", "LOINC", READY if (cov["LOINC (lab)"][0] or 0) >= 80 else PARTIAL, f"Lab coverage {cov['LOINC (lab)'][0] or 0}%."),
        _i("tm_other", "Other terminology (ICD-10 base catalog, HPT product codes, ATC)", READY),
    ]))
    return sections


def score(sections):
    out = []
    for title, items in sections:
        ready = sum(1 for i in items if i["status"] == READY)
        out.append((title, items, round(100.0 * ready / len(items), 1) if items else 0.0))
    total_ready = sum(1 for _, items in sections for i in items if i["status"] == READY)
    total = sum(len(items) for _, items in sections)
    return out, round(100.0 * total_ready / total, 1) if total else 0.0
