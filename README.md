# MediCore HMIS — Foundation (Phase 1)

A multi-tenant Hospital Management Information System foundation for Kenyan
health facilities (Level 1–6). This first phase delivers the **core** that
every other module (patients, prescriptions, lab, radiology, billing) will
plug into: authentication, hospitals/branches, role-based access control,
per-hospital settings, an audit trail, and starter master catalogs
(diagnosis codes, drug formulary, radiology tests, insurance schemes).

## Stack
Flask 3, Flask-SQLAlchemy, Flask-Login, Flask-WTF (CSRF), SQLite (dev) —
Tailwind CSS via CDN, vanilla JS + `fetch()` for AJAX (no build step needed,
works fine in Termux).

## Quick start (Termux / PythonAnywhere / any Linux)

```bash
pip install -r requirements.txt --break-system-packages   # Termux
# or: pip install -r requirements.txt                     # PythonAnywhere venv

python seed.py                                    # roles, permissions, demo org + demo logins
python import_icd10.py icd10_source.xlsx --replace  # full ICD-10-CM diagnosis catalog
python run.py                                     # http://127.0.0.1:5000
```

### Onboarding a new, unrelated hospital or hospital group
They don't need any of the above run on their behalf — send them to
`/auth/register` (linked from the login page). It creates their own
isolated `Organization`, first `Hospital`, and founding CEO account, with
a starter formulary/lab menu/radiology menu/insurance list/wards ready to
edit. Completely separate from the demo org and from every other
organization on the same install — see "Phase 13" below for what's
actually isolated vs. shared.

### Demo logins (all use password `Password123!`)
| Username | Role | Sees |
|---|---|---|
| `ceo` | CEO | Every hospital in the organization |
| `manager` | Hospital Manager | Their assigned hospital only |
| `admin` | Admin | Their hospital's settings, users, catalogs |
| `doctor` | Doctor | Their hospital, clinical actions only |
| `nurse`, `pharmacist`, `labtech`, `radiologist`, `records`, `billing` | Department staff | Their hospital, department-scoped |

Every seeded user has `must_change_password = True` — wire up a "force
password change on first login" screen before going live.

## How the access model works

- **Organization** — the top-level owner (you / the hospital chain).
- **Hospital** — one facility/branch (`level` = Level 1–6). Everything
  clinical hangs off `hospital_id`.
- **Role.scope**:
  - `organization` → CEO. Sees every hospital under the organization.
  - `hospital` → Hospital Manager / Admin. Sees only their assigned
    hospital(s) — a manager overseeing more than one branch gets extra rows
    in `UserHospitalAccess`.
  - `department` → clinical/support staff. Same hospital scope, but their
    permission set is narrower (a nurse can't dispense drugs, a pharmacist
    can't edit hospital settings, etc).
- **Permission** codes (e.g. `pharmacy.dispense`, `catalogs.manage`) are
  attached to roles, not users, so changing what a role can do updates
  everyone with that role instantly. Check `seed.py` for the full list and
  `app/decorators.py` for how routes enforce it (`@permission_required(...)`).
- **AuditLog** records every login/create/update — required for
  medical-legal traceability. Extend `log_action()` calls as you add modules.

## What's already wired up
- AJAX login (JSON in/out, no page-reload flicker)
- CSRF protection on all state-changing requests (token lives in a meta tag,
  `postJSON()` in `base.html` attaches it automatically)
- Hospital settings page (name, contact info, per-hospital fee defaults)
  editable by that hospital's Admin/Manager
- Hospital/branch creation (CEO only)
- Staff creation with temp password + forced reset flag
- Starter catalogs: 10 ICD diagnosis codes, 7 drugs, 5 radiology tests, 4
  insurance schemes (SHA/NHIF, private, cash) — add more from the Catalogs
  screen or bulk-load a real ICD-10/KEMSA list into `seed.py`

## Phase 2 — Patients (done)
- `Patient` (bio-data, scoped to hospital) + `Visit` (one row per check-in,
  status: Waiting → In Consultation → Completed / Admitted → Discharged)
- Auto-generated patient numbers: `<HOSPITAL_CODE>-<YEAR>-<00001>`
- Registration form doubles as first check-in (captures next of kin +
  insurance up front, since Kenyan facilities need that at OP desk anyway)
- Search by name / patient number / phone / national ID
- Patient detail page shows full visit history and lets staff change visit
  status or check a returning patient in for a new visit
- Enforced via `patient.view` (most roles) and `patient.register` (Doctor,
  Nurse, Records Officer) — Pharmacist etc. can look patients up but not
  register new ones

## Phase 3 — Triage, Consultation & Inpatient Admission (done)
- **Triage**: vitals (temp, pulse, BP, resp. rate, SpO2, weight, height →
  auto-computed BMI) + priority (Emergency/Urgent/Normal), one record per
  visit. Nurse or Doctor can record it; moves visit status `Waiting → Triaged`.
- **Consultation**: chief complaint, exam notes, diagnosis picked from the
  `DiagnosisCode` (ICD) catalog, treatment plan, follow-up date. Doctor-only
  (`consultation.create`). A visit needs a triage record before it can be
  consulted on — that ordering is enforced in the UI.
- **Inpatient admission**: if the visit type is Inpatient, the consultation
  form has an "Admit this patient" checkbox → pick a `Ward`, bed number,
  expected discharge date. Ward bed counts (`available_beds`) update live
  as patients are admitted/discharged — tested end-to-end including a ward
  hitting zero free beds.
- **Discharge**: closes the admission and the visit in one action.
- Outpatient visits that aren't admitted auto-close to `Completed` right
  after consultation; inpatient visits move to `Admitted` until discharged.
- **Wards** are now a hospital-scoped catalog, managed from
  Admin → Catalogs alongside diagnosis/drug/radiology/insurance (4 sample
  wards seeded: General, Maternity, ICU, Pediatric).

Visit status flow is now: `Waiting → Triaged → In Consultation → Completed`
(outpatient) or `Waiting → Triaged → Admitted → Discharged` (inpatient).

## Phase 4 — Clinical History, Lab/Radiology Ordering & Multi-Patient Flow (done)
- **Clinical Summary panel** on the patient page: allergies (shown as a red
  banner the moment the page loads — impossible to miss), chronic
  conditions, full diagnosis history across every past visit, and a recent
  vitals trend table. This is what keeps a doctor oriented on a patient
  they may not have seen before.
- **Lab & Radiology ordering is now decoupled from finishing the
  consultation.** A doctor can order a lab test or imaging study straight
  from the visit card at any point after triage — it queues instantly for
  the Lab Technician / Radiologist and does **not** block anything.
- **Consultation now has two save modes**: "Save & See Next Patient"
  (draft — keeps the visit open in `In Consultation`, so the doctor is free
  to open any other patient immediately) and "Complete Consultation"
  (finalize — requires a diagnosis to be selected, then closes the visit to
  `Completed` or `Admitted`). Tested: finalizing without a diagnosis is
  correctly rejected.
- **Patient Queue** (`/queue`) — every Waiting/Triaged/In Consultation visit
  across the doctor's/nurse's hospital(s), sorted by triage priority
  (Emergency → Urgent → Normal) then wait time. A visit shows an "Awaiting
  results" badge if it has any lab/radiology order still pending, so staff
  know at a glance who's blocked on results vs. who's just next in line.
- **Lab Worklist** (`/lab/worklist`, Lab Technician) and **Radiology
  Worklist** (`/radiology/worklist`, Radiologist) — pending orders across
  the hospital, with a one-click status update and a result/findings form.
  Results and findings then surface automatically on the patient's visit
  card the next time a doctor opens it.
- New `Patient.allergies` / `Patient.chronic_conditions` fields, captured
  at registration.
- New master catalog: **Lab Tests** (8 sample tests seeded: CBC, MRDT,
  Blood Slide, RBS, U&E/Creatinine, Urinalysis, HIV rapid test, Stool
  microscopy) — managed the same way as diagnosis/drug/radiology/insurance
  from Admin → Catalogs.

Tested end-to-end: nurse registers a patient with a penicillin allergy →
triages them → doctor orders a CBC and a chest X-ray → saves a consultation
draft (visit stays open) → doctor registers and starts seeing a *second*
patient without anything blocking → both patients show correctly on the
shared queue → lab tech and radiologist independently resolve their orders
from their own worklists → doctor reopens the first patient, sees both
results inline, and finalizes the consultation (diagnosis required) →
visit closes to `Completed` and drops off the queue while the second
patient remains active.

## Phase 5 — Real ICD-10-CM Diagnosis Catalog (done)
- Imported the full CMS Section 111 FY2026 valid ICD-10-CM code list:
  **73,888 diagnosis codes**, formatted to the standard dotted display
  (`A00.0`, not the source file's undotted `A000`).
- `import_icd10.py` — re-runnable importer for future CMS updates
  (they publish a refreshed file most Octobers):
  ```bash
  python import_icd10.py icd10_source.xlsx --replace   # wipes & reloads
  python import_icd10.py new_file.xlsx                  # adds only new codes
  ```
  The source file used for this import ships alongside it as
  `icd10_source.xlsx` for reference.
- **A 74k-row `<select>` would have made the consultation page multi-
  megabytes and unusable, so the diagnosis field is now a type-ahead
  search box** (`/api/diagnosis-search?q=...`, gated behind
  `consultation.create`) — type a name or code, pick from up to 20 live
  matches. Tested: searching "malaria" correctly surfaces every
  Plasmodium species/complication variant; the patient page itself stays
  ~20KB regardless of catalog size.
- The 10 illustrative sample codes from earlier phases were replaced by
  the real dataset (fresh installs via `seed.py` still get the small
  starter set — run the importer afterward to load the full catalog).

## Phase 6 — Structured History Taking (done)
- Consultation now follows the standard clerking structure instead of a
  single free-text box: **Chief Complaint → History of Presenting Illness
  → Past Medical History → Past Surgical History → Drug History → Family &
  Social History → Review of Systems**, then Examination, then Assessment
  & Plan (diagnosis + treatment plan + follow-up).
- The form surfaces the patient's on-file allergies/chronic conditions and
  their triage vitals right alongside the history fields, so the doctor
  isn't flipping between the visit card and the form to stay guided.
- HPI and exam findings now show inline on the visit card summary once
  saved (not just the final diagnosis), so past clerking is visible at a
  glance without reopening the form.
- All fields save with both "Save & See Next Patient" (draft) and
  "Complete Consultation" (finalize) — tested end-to-end with a full
  appendicitis workup (HPI, PMH, PSH, drug/family/social history, ROS,
  exam findings, diagnosis via the ICD-10 search, treatment plan).

## Phase 7 — Prescription & Pharmacy (done)
- **Prescribing**: "+ Prescribe" on the visit card opens a form where the
  doctor can add one or more drugs (typeahead search — same pattern as
  diagnosis search, so it's ready to scale if you bulk-load the KEMSA
  essential medicines list later), each with dosage, frequency (OD/BD/
  TDS/QDS/STAT/PRN), duration, quantity, and instructions. Sends straight
  to the pharmacy worklist — doesn't block the visit, same as lab/radiology.
  The patient's on-file allergies show right in the prescribe modal.
- **Stock**: `StockBatch` per hospital/drug (batch number, quantity, unit
  cost, selling price, expiry date), received from Admin/Pharmacist via
  Pharmacy → Stock. Stock overview flags any drug under 10 units as LOW.
- **Dispensing is FEFO** (first-expire-first-out): when a pharmacist
  dispenses, stock is drawn from the earliest-expiring batch with quantity
  left first, so nothing quietly expires on the shelf while a newer batch
  sits untouched. Tested directly: two batches (30 units expiring first,
  20 expiring later) — dispensing 30 fully drained the sooner batch and
  left the later one untouched.
- **Partial dispensing** is a first-class case, not an edge case — a
  pharmacist can dispense less than prescribed if stock is short, and the
  item sits as "Partially Dispensed" until the rest is filled. Tested:
  dispense 30 of 35 → status `Partially Dispensed`, remaining 5 → dispense
  the last 5 → status `Dispensed`, drops off the worklist.
- **Out-of-stock dispensing is blocked** with a clear error rather than
  silently going negative — tested against a drug with zero stock.
- Visit's "Awaiting results" badge (queue page) now also fires for
  pending/partially-dispensed prescriptions, not just labs/radiology.
- New permission `pharmacy.stock` (Pharmacist role) separate from
  `pharmacy.dispense`, in case you want a stock clerk who can't dispense
  or a pharmacist who can't touch stock levels.

## Phase 8 — Billing & Insurance Claims (done)
- **`Bill` generated from real activity, not estimates** — one click on
  a completed/admitted/discharged visit pulls in: the consultation fee
  (hospital setting), every *dispensed* pharmacy item at its actual batch
  selling price, every *resulted* lab test, every *reported* radiology
  study, and inpatient bed charges (`bed days × ward daily rate`).
  Anything not yet done (pending lab, undispensed drugs) simply isn't
  billed yet — regenerating later picks up new charges automatically,
  as long as no payment has been recorded yet.
- **Payments**: multiple partial payments per bill (Cash, M-Pesa, Card,
  Insurance), each with an optional reference (e.g. M-Pesa code). Bill
  status auto-updates: Pending → Partially Paid → Paid. Overpayment past
  the balance due is rejected. Bills can also be **waived** (e.g. an
  insurance claim fully covers it) with a notes field.
- **Insurance**: a bill inherits the patient's insurance scheme at
  generation time, with an editable claim number field for NHIF/SHA
  reference tracking.
- **Billing Worklist** (`/billing/worklist`, Billing/Insurance Clerk) —
  every bill still owed money, across the hospital.
- Catalog prices added: `LabTest.price`, `RadiologyTest.price`,
  `Ward.daily_rate`, plus the hospital setting
  `default_consultation_fee` (KES 500 in the seeded demo hospital) — all
  editable from Admin → Catalogs / Settings.
- Tested end-to-end: an outpatient malaria visit (consultation + 10 units
  of dispensed Amoxicillin + a resulted CBC) billed correctly at
  KES 1,150, paid in two installments, correctly rejected an overpayment,
  and dropped off the worklist once fully paid. A 3-day ICU admission
  (backdated to simulate a real stay) billed correctly at
  KES 24,500 and was waived for an insurance-covered case.

## Phase 9 — Real Dashboards for Every Role (done)
Dashboards were placeholder text before this phase. Now every role's
landing page pulls live numbers:
- **CEO**: org-wide summary cards (hospitals, staff, visits today, revenue
  today, outstanding) plus a per-hospital breakdown (staff, visits today,
  queue size, bed occupancy, revenue, outstanding, low-stock warning).
- **Hospital Manager**: the same operational metrics scoped to their
  hospital(s) — staff, visits today, live queue size, bed occupancy,
  revenue today/this month, outstanding bills, plus pending-work badges
  for lab/radiology/pharmacy and a low-stock flag.
- **Department staff**: cards tailored to what their role actually acts
  on, each one a live link — Doctor/Nurse see queue size and jump straight
  to the Patient Queue; Lab Tech/Radiologist see their pending order count
  and jump to their worklist; Pharmacist sees pending prescriptions *and*
  low-stock count; Billing Clerk sees unpaid bill count and total
  outstanding. A role with no relevant permission just doesn't get an
  irrelevant card instead of showing a blank one.
- Verified against real data: registered patients, low stock, and pending
  orders all showed the correct counts on each role's dashboard.

## Phase 10 — Medical Documents & Patient Flow (done)
- **Medical documents**: Sick Off, Medical Certificate, Referral Letter,
  Discharge Summary — one flexible model (`MedicalDocument`), issued by a
  doctor from the visit card ("+ Document"). Each gets a sequential
  reference number per hospital per type per year (`SO-NRB-01-2026-0001`,
  `REF-...`, `MC-...`, `DS-...`).
- **Printable letterhead page** (`/documents/<id>/print`) — hospital
  name/contact as the header, patient details, the document body laid out
  per type (sick-off period + days computed automatically, referral
  facility/doctor, discharge ward/dates pulled from the admission record),
  and a signature block with the issuing doctor's name and role. Uses the
  browser's native print-to-PDF (a "Print / Save as PDF" button) rather
  than a server-side PDF library, so it works the same on PythonAnywhere's
  free tier without extra dependencies.
- Tested: a Sick Off correctly computed a 4-day period from the two dates
  entered, and a Referral Letter correctly carried the receiving facility
  and doctor onto the printout. Non-doctor roles are blocked from issuing
  documents (tested against Nurse — 403).

- **Patient flow visibility** — `Visit.current_stage` is a computed
  "what's next" string (Awaiting triage → Awaiting doctor → Awaiting lab
  & pharmacy → Awaiting diagnosis → Awaiting billing → Awaiting payment →
  Fully settled) shown on both the Patient Queue and the patient's own
  page, replacing the old generic "Awaiting results" badge with something
  that actually tells staff what to do next. Tested the transition from
  "Awaiting triage" → "Awaiting doctor" as a visit moves through triage.
- **Billing worklist now surfaces visits nobody has billed yet** —
  previously a Completed/Discharged visit with no bill generated would
  just sit there invisibly. The worklist now has an "Awaiting Billing"
  section listing exactly those, with a one-click Generate Bill button.
  The same unbilled count now shows on the Billing Clerk's dashboard card
  and the Hospital Manager's per-hospital badge row. Tested end-to-end:
  a completed visit showed up in Awaiting Billing, got billed with one
  click, and dropped off the list immediately after.

## Phase 11 — Professional Refactor: Consolidated Consultation & Department-Scoped Queues (done)
This was a deliberate "clean it up" pass rather than a new module — the
app had grown enough separate buttons/modals per visit that it felt
scattered rather than professional. Two structural fixes:

**1. Lab, radiology, and prescriptions now live inside the Consultation
workflow, not as separate scattered buttons.** Previously a doctor had
four disconnected entry points on the visit card (Consultation, Order Lab,
Order Radiology, Prescribe) — now there's one "Consultation" action.
Opening it gives History Taking → Examination → **Investigations** (add
any number of lab tests and imaging studies inline) → **Prescriptions**
(same drug typeahead as before, now embedded) → Assessment & Plan. "Save &
See Next Patient" fires off whatever lab/radiology/drug lines were added
alongside the note, without requiring a diagnosis; "Complete Consultation"
still requires one. This matches how a real encounter actually works —
everything ordered in one sitting, not through separate popups. The
underlying `LabOrder`/`RadiologyOrder`/`PrescriptionItem` records and the
Lab/Radiology/Pharmacy worklists that consume them are unchanged, so
nothing downstream broke — verified with a full test ordering a CBC,
chest X-ray, and Amoxicillin all through one consultation draft, then
watching them appear correctly on the Lab Worklist and Pharmacy Worklist.

**2. Every department now sees only their own queue — not everyone
else's.** The old `/queue` page showed all Waiting + Triaged + In
Consultation visits to anyone with either `triage.create` or
`consultation.create`, so a nurse would see patients already with the
doctor and vice versa. It's now one route that returns a different,
correctly-scoped result per role: a nurse gets a **Triage Queue** (Waiting
only), a doctor gets a **Consultation Queue** (Triaged + In Consultation
only) — titled accordingly in the nav and on the page itself. As part of
tightening role separation, `triage.create` was also removed from the
Doctor role's default permissions (a doctor triaging their own patients
isn't the intended division of labor at a facility big enough to have
both roles) — tested directly: a doctor can no longer see the `+ Triage`
button, and each queue correctly excludes the other's patients. The
Lab/Radiology/Pharmacy/Billing worklists were already correctly scoped to
their own pending work and needed no changes.

Dashboards were updated to match: the "In Queue Now" card now reads "In
Triage Queue" or "In Consultation Queue" depending on the viewer's role,
with the count matching exactly what that role's queue page shows.

## Phase 12 — Printable Prescription Slips & Bill Receipts (done)
Same letterhead pattern as the medical documents module, applied to the
two other paper artifacts a facility hands patients daily:
- **Prescription slip** (`/prescriptions/<id>/print`) — hospital
  letterhead, patient details (pulling weight from the visit's triage
  record automatically), an allergy warning banner if any are on file,
  and the full drug table (dosage, frequency, duration, quantity,
  instructions), signed by the prescribing doctor. Linked from each
  prescription on the patient's visit card.
- **Bill receipt** (`/bills/<id>/receipt`) — itemized charges, full
  payment history with method/reference, and amount paid/balance due.
  Titled "Payment Receipt" once fully paid, "Statement" otherwise, with a
  note when a bill was waived. Linked via a "Print Receipt" button on the
  bill detail page.
- Both tested end-to-end: a prescription slip correctly pulled the
  patient's weight from triage and showed their penicillin allergy; a
  receipt for a fully-paid bill correctly showed "Payment Receipt" with
  the Cash payment listed. Unauthenticated access to both is blocked
  (redirects to login).

## Phase 13 — True Multi-Tenant SaaS: Unrelated Hospitals, Fully Independent (done)
Until this phase, the system was only "multi-tenant" at the surface —
hospitals/patients/staff were correctly scoped by `hospital_id`, but four
catalog tables (`Drug`, `LabTest`, `RadiologyTest`, `InsuranceScheme`)
were **global**, meaning any hospital's admin adding a custom drug or lab
test would silently become visible and usable by every *other*,
completely unrelated organization on the same install. That's fixed now,
and self-service signup means you don't have to run `seed.py` by hand for
every new customer.

- **`organization_id` added to `Drug`, `LabTest`, `RadiologyTest`,
  `InsuranceScheme`**, with uniqueness constraints changed from global to
  per-organization. Every catalog page, search endpoint, dispense/order
  route, and dashboard stat now filters by the current user's
  `organization_id`. Order/prescription creation routes also validate
  that the drug/lab test/radiology test being referenced actually
  belongs to the requester's own organization (defense against a crafted
  request trying to reference another org's catalog ID).
- **`Hospital.code` and `MedicalDocument.document_number` uniqueness
  changed from global to per-organization** — two unrelated hospitals can
  now both use "NRB-01" or whatever short code they like without
  colliding, since patient numbers and document numbers were already
  scoped by `hospital_id` underneath.
- **`DiagnosisCode` (ICD-10) stays global on purpose** — it's a shared
  medical standard, not tenant data, so every organization benefits from
  the same 73,888-code catalog without needing to import it themselves.
- **Self-service organization signup** (`/auth/register`, linked from the
  login page) — a totally new, unrelated hospital or hospital group can
  create their own isolated `Organization` + first `Hospital` + founding
  CEO account in one form submission, and immediately gets a starter
  formulary/lab menu/radiology menu/insurance list/wards (via the new
  `app/onboarding.py`, shared with `seed.py` so there's one source of
  truth for starter data). No manual setup needed on your end per
  customer.
- Fixed a latent bug where the CEO account had `hospital_id = None`,
  which broke the Settings page (it looks up `current_user.hospital`).
  CEOs now get `hospital_id` set to their primary hospital on creation —
  their organization-wide access (which comes from `Role.scope`, not
  `hospital_id`) is unaffected.
- **Tested end-to-end with two independent organizations** (Coastal
  Health Group and Rift Valley Medical Trust), both deliberately using
  the *same* hospital short code (`MSA-01`) to confirm no collision:
  registered both via the signup form, added a custom drug/lab
  test/insurance scheme to Org A, and confirmed none of it appeared in
  Org B's catalog. Registered a patient in each org and confirmed
  cross-org search returns nothing ("No patients found") while each
  org's own staff see their own patients normally. Confirmed both orgs
  retain full, shared access to the ICD-10 catalog throughout.

## Phase 14 — Comprehensive KEMSA-Aligned Drug Catalog, Full Lab/Radiology Menus, Staff Registration Lockdown (done)
- **246 drugs** — up from the original 7-item placeholder. Sourced
  directly from the official **Kenya Essential Medicines List (KEML)
  2023**, Ministry of Health, for the sections the fetch could reach in
  full: anaesthetics, pain/palliative care, antiallergics, antidotes,
  anticonvulsants, the full anti-infective range (antibacterials by
  AWaRe class, antituberculosis, antifungals, antivirals/ARVs,
  antimalarials, anthelminthics), antimigraine, antiparkinsonism, blood/
  haematinics/anticoagulants, and cardiovascular medicines. The remaining
  categories (dermatological, GI, endocrine, vaccines, ophthalmological,
  reproductive health, mental health, respiratory, ENT, rheumatology,
  vitamins, IV fluids/electrolytes, blood products) are supplemented from
  standard WHO Model List / Kenya EML staple medicines, since the source
  fetch couldn't reach those sections. **If you have the full KEML as a
  spreadsheet or PDF, send it over and this can be re-imported with the
  same exact fidelity as the ICD-10 catalog was.**
- **89 lab tests** across Hematology, Coagulation, Clinical Chemistry,
  Microbiology, Parasitology, Serology/Immunology, Endocrine/Hormones,
  Tumor Markers, Urinalysis, Histopathology/Cytology, and Blood Bank.
- **42 radiology/imaging tests** across X-Ray, Ultrasound (incl.
  Doppler, echo, FAST), CT, MRI, Mammography, and Fluoroscopy (barium
  studies, IVU, HSG), plus DEXA.
- This is now the **default starter catalog for every new organization**
  signing up via `/auth/register` — `app/onboarding.py` is the single
  source of truth, shared by `seed.py` and the signup flow.
- **`refresh_catalog.py`** — a one-time backfill script for any
  organization that was created *before* this expansion (so you don't
  have to manually re-enter everything for existing customers). Safe to
  re-run: skips anything that already exists by name (drugs) or code
  (lab/radiology tests), and cleans up a handful of early-prototype
  entries whose naming didn't match the final list (e.g. "ORS" →
  "Oral Rehydration Salts"). Tested directly: simulated a pre-existing
  organization with the old 7/8/5-item catalog, ran the script, and
  confirmed it added exactly the missing 241/84/38 items with zero
  duplicates by name or code.
  ```bash
  python refresh_catalog.py <organization_id>
  python refresh_catalog.py --all      # every organization on the install
  ```

- **Staff registration confirmed restricted to Hospital Manager, Admin,
  and CEO only** — this was actually already enforced by the existing
  `users.manage` permission (never granted to Doctor, Nurse, Pharmacist,
  Lab Technician, Radiologist, Records Officer, or Billing Clerk), but it
  hadn't been directly tested end-to-end until now. Verified live: all
  seven non-manager roles get a 403 on both viewing and submitting to
  `/admin/users` (tested with a real, valid CSRF token to rule out a
  false pass from an unrelated CSRF rejection), while Hospital Manager,
  Admin, and CEO succeed normally.

## Phase 15 — Missing Inpatient Features, IntaSend Subscription Billing, Level-Gated Services, Typeahead for Lab/Radiology (done)

**Missing inpatient features, filled in:**
- **Bed-level tracking** — a new `Bed` model lets a ward have individually
  labelled beds (e.g. "ICU-01", "ICU-02") with their own status
  (Available/Occupied/Maintenance). Set up from Admin → Catalogs. A ward
  with beds defined requires picking a *specific* bed at admission time
  (no more silent double-booking); a ward with no beds set up still falls
  back to the old free-text bed number, so nothing breaks for wards you
  haven't configured this way yet.
- **Ward transfer** — a new "Transfer" button on an active admission moves
  the patient to a different ward/bed, automatically freeing the old bed
  and occupying the new one, with a full audit trail (`WardTransfer`)
  showing who moved them, when, and why. Tested: transferred a patient
  from a specific ICU bed to General Ward A (which has no individual beds
  configured, so it correctly fell back to the free-text label) and
  confirmed the old bed flipped back to Available.
- **Repeated inpatient vitals** (`InpatientVitals`) — distinct from the
  one-off triage vitals at check-in, nurses can log vitals on every ward
  round for the duration of a stay. Shown as a running log on the visit
  card.
- **Nursing notes** (`NursingNote`) — free-text ward-round entries,
  separate from the doctor's one-time consultation notes, authored and
  timestamped per entry.
- All four gated correctly: vitals/notes need `triage.create` (nurses),
  transfer needs `consultation.create` (doctors), bed setup needs
  `catalogs.manage` (Admin/Manager/CEO).

**Level-gated services** — enforced server-side, not just hidden in the
UI:
- A new `app/level_policy.py` defines what each KEPH facility level may
  do: Level 1–2 facilities can't admit inpatients at all; Level 3+ can;
  radiology modalities scale from X-Ray only (Level 2) up through
  Ultrasound (Level 3+), CT (Level 4+), to MRI/Mammography/Fluoroscopy
  (Level 5+).
- Enforced at patient registration/check-in (Inpatient visit type is
  hidden from the form *and* rejected server-side if forced via a direct
  API call), at consultation admission, at radiology ordering (a Level 2
  facility's radiology search returns zero MRI/CT results, full stop),
  and at ward creation.
- Tested directly: registered a new Level 2 organization, confirmed the
  registration form has no "Inpatient" option, then deliberately forced
  `visit_type=Inpatient` via a raw API call and confirmed the server
  rejected it — the restriction can't be bypassed by skipping the UI.
  Confirmed a Level 4 facility can find CT scans but a direct search for
  "MRI" returns nothing.

**IntaSend subscription billing** — this is billing the *organization*
for using the software, separate from the organization's own patient
billing (`Bill`/`Payment`):
- **3-day free trial** starts automatically on signup
  (`Organization.trial_ends_at`), full access during that window.
- **Price varies by hospital level** (`SUBSCRIPTION_PRICING` in
  `app/models.py` — Level 2 KES 3,000/mo up to Level 6 KES 40,000/mo;
  adjust freely, it's just a lookup table).
- **A global access gate** (`before_request` in `app/__init__.py`) blocks
  every route except login/registration/the subscription page itself once
  an organization's trial or paid period lapses — tested by backdating a
  trial's expiry and confirming every route redirects to `/subscription`
  while the subscription page itself stays reachable so they can actually
  pay.
- **Real IntaSend integration** (`app/subscription/intasend_client.py`,
  using the official `intasend-python` SDK) — M-Pesa STK push initiated
  from `/subscription`, with webhook handling
  (`/subscription/webhook`, challenge-verified) to confirm payment and
  extend `current_period_end` by 30 days, stacking on top of any
  remaining time rather than wasting it. **You'll need to set real
  credentials** (`INTASEND_SECRET_KEY`, `INTASEND_PUBLISHABLE_KEY`,
  `INTASEND_WEBHOOK_CHALLENGE`) as environment variables before this goes
  live — see the Environment Variables section below.
- **Dev-mode payment simulation** — when no real IntaSend credentials are
  configured, a "Simulate Payment" button appears so the full trial →
  expire → pay → reactivate cycle can be tested without live credentials.
  It disables itself automatically the moment real keys are set. Tested
  the complete cycle this way: new org signs up, trial gets backdated to
  expired, access is blocked, checkout correctly fails gracefully with
  the simulate option offered, simulating the payment restores access
  immediately, and the trial banner disappears once the subscription is
  active.
- The demo organization is seeded with a 10-year active subscription so
  it's never gated.

**Typeahead search for lab and radiology tests** — the consultation's
Investigations section now uses the same live-search pattern as
diagnosis/drug picking (`/api/lab-test-search`, `/api/radiology-test-search`)
instead of a plain dropdown, which matters now that the catalog has 89
lab tests and 42 radiology tests. The radiology search is level-filtered
server-side, so what a doctor can even find to search for is already
scoped to what their facility is allowed to do.

## Environment Variables
```bash
# IntaSend (subscription billing) — get these from your IntaSend dashboard
INTASEND_SECRET_KEY=your_secret_key
INTASEND_PUBLISHABLE_KEY=your_publishable_key
INTASEND_WEBHOOK_CHALLENGE=a_string_you_also_set_on_the_intasend_dashboard
INTASEND_TEST_MODE=true   # set to false for live/production payments
```
Without these set, the subscription page still works fully via the
dev-mode payment simulator — nothing crashes, it just can't take real
money yet.

## Phase 16 — Outright Purchase Option & Multi-Diagnosis Support (done)

**Sell the system outright, no monthly payments** — a second commercial
path alongside the existing lease/subscription model:
- `Organization.subscription_status` now supports a third state,
  `purchased`, alongside `trial`/`active`. A purchased organization has
  **no expiry to track at all** — `current_period_end` stays `None` and
  the access gate never re-checks it, permanently.
- New `ONE_TIME_PRICING` table in `app/models.py` (Level 2 KES 60,000 up
  to Level 6 KES 850,000 — adjust freely, same pattern as the monthly
  pricing table, roughly anchored around 18-24 months of the equivalent
  subscription).
- The `/subscription` page now shows **both options side by side** —
  "Monthly Subscription" and "Buy Outright" — each with its own price and
  its own M-Pesa checkout flow (same IntaSend integration, `plan_type`
  distinguishes which one a given `SubscriptionPayment` was for). Once
  purchased, both payment cards disappear entirely and the page just
  shows "Owned outright — no monthly payments, ever."
- Tested directly: registered a new org, purchased outright via the dev
  payment simulator, confirmed `is_purchased` is true with no expiry
  date, confirmed the access gate never blocks them, confirmed the trial
  banner is gone, and confirmed the payment options disappear since
  there's nothing left to sell them. Ran the monthly path in parallel on
  a separate org to confirm it's unaffected — both models coexist
  cleanly, and a subscribed org can still see the option to switch to a
  permanent purchase later.

**Multiple diagnoses per consultation** — real patients often have more
than one active diagnosis (e.g. malaria plus known hypertension):
- New `ConsultationDiagnosis` model holds any number of secondary/comorbid
  diagnoses; the primary diagnosis stays on `Consultation.diagnosis_code_id`
  as before (still required to finalize) so nothing that already reads
  "the" diagnosis broke.
- The consultation form's Assessment & Plan section now has a
  "+ Add another diagnosis" line, using the same ICD-10 typeahead search
  as the primary field, repeatable any number of times.
- **Re-saving a consultation fully replaces the secondary-diagnosis list**
  rather than accumulating duplicates — tested directly: saved with 2
  secondary diagnoses, re-saved with a different single one, confirmed
  the old two were gone and only the new one remained.
- `Consultation.all_diagnoses` gives the full primary + secondary list in
  one place for anything that wants to display or export it later
  (discharge summaries, billing code review, etc.).
- Visit card and consultation summary now show "Also: X, Y" alongside
  the primary diagnosis when secondary ones exist.

## Next modules (build in this order)
1. ~~Patient registration~~ ✅
2. ~~Triage & Consultation~~ ✅
3. ~~Inpatient admission / ward assignment~~ ✅
4. ~~Clinical history panel + lab/radiology ordering + patient queue~~ ✅
5. ~~Full ICD-10-CM diagnosis catalog~~ ✅
6. ~~Structured history-taking (HPI/PMH/PSH/drug/family/social/ROS)~~ ✅
7. ~~Prescription & Pharmacy (FEFO dispensing, stock batches)~~ ✅
8. ~~Billing & Insurance claims~~ ✅
9. ~~Real dashboards for every role~~ ✅
10. ~~Medical documents + patient flow visibility~~ ✅
11. ~~Consolidated consultation workflow + department-scoped queues~~ ✅
12. ~~Printable prescription slips & bill receipts~~ ✅
13. ~~True multi-tenant SaaS: independent, unrelated hospitals~~ ✅
14. ~~Comprehensive KEMSA drug/lab/radiology catalogs~~ ✅
15. ~~Inpatient bed tracking, transfers, vitals, notes; IntaSend
    subscription; level-gated services; lab/radiology typeahead~~ ✅
16. ~~Outright purchase option; multiple diagnoses per consultation~~ ✅
17. ~~Forced first-login password change; downloadable user manual~~ ✅
18. **MOH/DHIS2-style reporting exports**
19. **SMS/email notifications** (appointment reminders, lab results ready)
20. **Platform-level superadmin** — a way for *you* to see every
    organization on the install, suspend/reactivate one, or impersonate
    for support purposes

## Phase 17 — Forced Password Change & Downloadable User Manual
- **Every new staff account created via Admin → Staff & Roles is now
  actually forced to change their temporary password on first login** —
  the `must_change_password` field existed on the `User` model since
  Phase 1 but was never enforced until now. A global `before_request`
  gate redirects a flagged account to `/auth/change-password` before it
  can touch anything else, with clear messaging that they're on a temp
  password. Tested directly: created a real new staff account, confirmed
  they're blocked from every route (including a second, unrelated one)
  until they change it, confirmed wrong-current-password/mismatched-
  confirmation/too-short are all rejected with clear errors, and
  confirmed access is fully restored the moment a valid new password is
  set.
- **Demo/seed accounts are exempt from the forced change** —
  `must_change_password=False` for the bundled demo users, since they're
  for evaluating the system, not real onboarding. Real staff created
  through the actual admin flow are still fully gated. A "Change
  Password" link is also available any time from the sidebar, not just
  when forced.
- **Downloadable PDF user manual** (`generate_manual.py`, built with
  reportlab) — 18 pages covering every role's workflow: getting started,
  roles & permissions, patient registration, triage, consultation,
  pharmacy, lab/radiology, inpatient care, billing, medical documents,
  administration, dashboards, subscription, facility-level restrictions,
  and an FAQ. Downloadable from a "User Manual" link in the sidebar on
  every page, for every role (`/manual`). Re-run the generator script
  whenever a workflow changes meaningfully to keep it current.

## Things flagged as easy to forget (see chat for full list)
Audit trail (done), data-privacy/consent handling, inter-facility referral
(L1→L4→L6), offline tolerance for low-connectivity facilities, MOH/DHIS2
reporting exports, 2FA + session timeout on shared terminals (session
timeout is already configured — 20 min idle).

## Deploying to PythonAnywhere
1. Upload this folder (or `git clone` it if you push to GitHub first)
2. Create a virtualenv, `pip install -r requirements.txt`
3. Point the WSGI file's Flask app import at `run.app`
4. Swap `SQLALCHEMY_DATABASE_URI` in `config.py` for your PythonAnywhere
   MySQL URL once you're past prototyping on SQLite
5. Run `python seed.py` once from a Bash console to initialize the DB
