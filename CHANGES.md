# Inpatient overhaul + Flask-Migrate — what changed

## 1. Flask-Migrate
- `app/extensions.py`, `app/__init__.py`: wired up `Migrate`.
- `requirements.txt`: added `Flask-Migrate==4.0.7`.
- **On your machine**, since `hospital.db` already has real data:
  1. `pip install -r requirements.txt`
  2. `flask db init`
  3. `flask db migrate -m "add inpatient overhaul: doctor reviews, care plans, monitoring, insurance auth"`
  4. Check the generated migration in `migrations/versions/` looks right, then `flask db upgrade`
  - Don't call `seed.py`'s `db.create_all()` again after this — let migrations own schema changes from here on.

## 2. Direct admission from OPD (no more close-file-and-reopen)
- `app/clinical/routes.py` (`finalize_consultation`): the "Admit this patient" flow no longer requires
  `visit.visit_type == 'Inpatient'` up front. Checking the box now converts the visit to Inpatient and
  admits in one step, carrying the OPD consultation forward as `Admission.admitting_consultation_id`.
- `app/templates/patients/detail.html`: the admit checkbox now shows for any open visit, not just ones
  already typed as Inpatient.

## 3. New inpatient models (`app/models.py`)
- `DoctorReview` — ward-round entries; any doctor can add one, so a stay accumulates a multi-doctor trail.
- `CarePlan` — nursing cardex: problem / goal / interventions, status Active/Resolved/Discontinued.
- `MonitoringEntry` — one flexible table for Intake, Output, Blood Sugar, Pain Score, etc. (`chart_type` +
  numeric `value`), rather than a separate table per vital type.
- `Admission` gained: `admitting_consultation_id`, `insurance_authorized_amount`,
  `insurance_limit_reached`, `discharge_summary_document_id`, plus `running_bill_total()` and
  `refresh_insurance_limit_flag()`.
- `InsuranceScheme.default_credit_limit` — seeds a new admission's authorized amount automatically;
  billing can still override per-stay once the insurer's actual authorization comes through.

## 4. Billing accumulation + insurance threshold trigger
- `app/billing/routes.py`: new `sync_admission_insurance_flag(visit)` helper.
- Hooked into lab result entry, radiology report entry, and pharmacy dispensing
  (`app/clinical/routes.py`, `app/pharmacy/routes.py`) — each recalculates the admission's running total
  and flips `insurance_limit_reached` once the authorized amount is hit.
- **Assumption made**: this *flags* the stay (banner in the workspace UI, `insurance_limit_reached` in
  API responses) rather than hard-blocking further orders — a Level 2 clinic usually still needs to treat
  the patient while billing chases a top-up from the insurer. If you'd rather it actually block placing
  further insurance-billed orders once the limit is hit, that's a small follow-up change to the same
  hook points.
- Cash patients are never subject to a limit — the check is a no-op without an insurance scheme.

## 5. Auto-generated discharge summary
- `app/inpatient/routes.py` (`generate_discharge_summary`): builds the narrative from admission dates,
  admitting + secondary diagnoses, ward transfers, every doctor review, resulted labs/imaging, and care
  plan problems — then creates/updates a `MedicalDocument` of type "Discharge Summary" (reuses your
  existing document numbering + print template). Only `discharge_medications` and `discharge_advice`
  are typed by hand.

## 6. New inpatient admission workspace
- `app/templates/inpatient/admission.html` + matching routes in `app/inpatient/routes.py`: one page per
  admission with doctor reviews, cardex, vitals, I/O & sugar monitoring, nursing notes, ward transfer,
  insurance authorization banner, and discharge/discharge-summary actions — instead of the old small
  modals bolted onto the OPD visit page. Linked from `patients/detail.html` ("Open ward workspace →").
  The old inline vitals/notes/transfer modals on that page still work too (untouched), so nothing breaks
  if you're mid-shift when you deploy this.

## Not done in this pass (flagged, not silently skipped)
- Input/output and blood-sugar charts are recorded via the generic `MonitoringEntry` model/UI above,
  but there's no dedicated trend chart/graph yet — just a table of recent readings.
- No hard block on placing new insurance-billed orders once the limit is reached (see assumption above).
- Ward/bed dashboards elsewhere in the app haven't been updated to link into the new workspace yet
  (only the patient detail page links to it so far).

## 7. (Follow-up) Trend charts + ward census dashboard
- `app/inpatient/routes.py`: added a dependency-free SVG sparkline helper (`_sparkline`) — no Chart.js/CDN
  needed, since this app has none today and connectivity at a Level 2 clinic shouldn't be assumed. Shows
  the last 20 readings for Intake, Output, and Blood Sugar on the admission workspace.
- New **Ward Census** page (`/wards/census`, `inpatient/census.html`): every active ward, who's in which
  bed, admitting doctor, day of stay, and insurance status at a glance, each row linking straight into
  that patient's workspace. This didn't exist before — bed occupancy was only ever a single number on
  the dashboard, not a browsable list.
- Linked from: the sidebar nav (for anyone with triage/consultation permission), the staff dashboard's
  new "Bed Occupancy" stat card, and the admin Wards & Beds setup page.

## 8. (Fix) Nurses were locked out of the inpatient workspace entirely
The workspace *page* itself (`/admissions/<id>`) and the new Ward Census page were gated on
`consultation.create` only — a doctor permission. Nurses have `triage.create`, which already correctly
gated the vitals/care-plan/monitoring/nursing-note actions *inside* the page, but they could never get
past the page-level check to reach them.

- `app/decorators.py`: added `any_permission_required(*codes)` — passes if the user has any one of the
  listed permissions, for shared pages like this one.
- `app/inpatient/routes.py`: `admission_workspace` and `ward_census` now use
  `@any_permission_required("consultation.create", "triage.create")`.
- `app/templates/inpatient/admission.html`: the action buttons are now shown per role — nurses see
  Care plan / Vitals / I-O-Sugar / Nursing note; doctors see Doctor review / Transfer / Discharge — so
  no one sees a button that would 403 on click.

## 9. Order medication, labs, radiology, and procedures/services from the workspace — billed on discharge
Previously the workspace had no way to actually order anything against the stay — only OPD-style
consultation had order forms, which meant inpatient charges (beyond the bed rate) weren't accumulating
the way you asked for in the billing fix (#4).

- **Medication, Lab, Radiology**: these already had working endpoints
  (`/visits/<id>/prescriptions`, `/visits/<id>/lab-orders`, `/visits/<id>/radiology-orders`) — just no
  inpatient UI. Added typeahead order forms to the workspace that call the same endpoints, scoped to the
  admission's visit. Each still respects the existing `prescription.create` / `lab.order` / `radiology.order`
  permissions, so the buttons only show for roles that can actually place that kind of order.
- **Procedures/services** — new. Nothing covered ward-side procedures (dressing, catheter insertion,
  oxygen therapy, minor procedure fees, etc.) before. Added:
  - `ProcedureOrder` model (`app/models.py`): name, quantity, unit price, status (Ordered/Done/Cancelled),
    who ordered/performed it. Only `Done` items get billed — same "bill reflects what actually happened"
    rule the app already uses for lab/radiology.
  - New `"Procedure"` category in `BILL_LINE_CATEGORIES`, and `_build_line_items()` in
    `app/billing/routes.py` now pulls in every `Done` procedure on the admission.
  - Routes: `POST /admissions/<id>/procedures` (order — can mark "already performed" to bill immediately),
    `POST /procedures/<id>/complete`, `POST /procedures/<id>/cancel`.
  - Workspace UI: a form to log a procedure/service (with a "bill immediately" checkbox for the common
    case of something done on the spot), plus a running table of everything ordered on the stay and its
    status — pending ones show a "Mark done" action.
- All of these already flow through `sync_admission_insurance_flag()` from the earlier billing fix, so
  the insurance-authorization banner and running total on the workspace now reflect medication, labs,
  radiology, AND procedures — not just the bed charge.
- At discharge, `generate_bill` (unchanged) picks up everything the same way it already did for OPD
  visits — dispensed drugs, resulted labs, reported radiology, bed days, and now Done procedures — so
  the discharge bill is the full accumulated stay, generated in one action.

## 10. (Fix) 500 on generate-bill: `TypeError: unsupported operand type(s) for +: 'float' and 'decimal.Decimal'`
`ProcedureOrder.amount` was computing `quantity * unit_price` in plain `float`. Every other
`BillLineItem.amount` on a bill comes straight from a `Numeric` column and stays `Decimal`.
`Bill.total_amount` sums all of them together — and Python's `Decimal` type raises `TypeError` rather
than silently coercing when you add it to a `float`, so a bill with even one procedure line on it broke
`generate_bill` outright.

- `app/models.py`: imported `Decimal`, and `ProcedureOrder.amount` now does the multiplication in
  `Decimal` (converting `quantity`/`unit_price` via `Decimal(str(...))` first, which is safe regardless
  of whether they're currently held in memory as `float` or `Decimal`) instead of `float`.

## 11. Pharmacy: drug substitution + dosage adjustment
Previously a pharmacist could only dispense exactly what was prescribed. Now, at dispense time, they can
optionally give a different drug and/or adjust the dosage — for an allergy, a known side effect, "we've
tried this before and it didn't work," or simply being out of stock — with a required reason, and the
original prescription is never overwritten (what the doctor ordered and what the patient actually got
both stay on the record).

- `app/models.py`: `PrescriptionItem` gained `dispensed_drug_id`, `dispensed_dosage`,
  `substitution_reason`, plus `effective_drug` / `effective_dosage` / `is_substituted` /
  `is_dosage_adjusted` helpers.
- `app/pharmacy/routes.py` (`dispense_item`): accepts an optional substitute drug + adjusted dosage;
  when substituting, stock is now drawn (and billed) from the **substitute** drug's batches, not the
  originally prescribed one. A reason is required whenever the drug itself is swapped.
- `app/templates/pharmacy/worklist.html`: the dispense dialog now shows the patient's recorded
  **allergies** and what they've been **given before** (their dispensing history), and has a toggleable
  "give a different drug or dosage" section with a drug-search field.
- Also fixed: the Dispense button used to be disabled outright whenever the *prescribed* drug had zero
  stock — which meant there was no way to reach the substitution option in exactly the situation it's
  most needed for. It's no longer disabled on stock alone.
- **Not built**: an automated drug-interaction/allergy-checking engine. That needs a real clinical drug
  database to be safe, and guessing at interaction data would be worse than not having the feature —
  this instead gives the pharmacist the patient's allergy note and prior-dispensing history to make that
  call themselves, same as the "has been used before" part of the request.

## 12. Discharged patients move to an archive
`Patient` gained `is_archived` (true once their most recent visit is Completed/Discharged, with nothing
else open). The main Patients list defaults to **Active** and adds an **Archive** tab for everyone else.
No flag to remember to flip: the moment a patient checks in again (a revisit or a fresh admission), that
new visit becomes their latest one and they reappear in Active automatically. Nothing is deleted or
hidden from search within a patient's own record — this only changes what shows up in the default list.

- `app/models.py`: `Patient.latest_visit` / `Patient.is_archived`.
- `app/patients/routes.py` (`list_patients`): filters on `is_archived`, toggled by `?archived=1`.
- `app/templates/patients/list.html`: Active / Archive tabs.

## 13. Pricing & System Settings — one place, system owner only
Pricing was scattered (a `default_consultation_fee` anyone with `settings.edit` could change, a
hardcoded low-stock threshold, insurance authorization defaults with no UI at all). Consolidated the
money-related knobs into one page restricted to a new `pricing.manage` permission — assigned **only** to
the CEO role, not Hospital Manager or Admin (both of whom keep `settings.edit` for ordinary hospital
contact info — name, address, phone, receipt footer — which isn't pricing).

- **New permission**: `pricing.manage` (`seed.py`) — CEO role only. **You'll need to re-run
  `python seed.py` after pulling this** so the permission exists and gets attached to the CEO role; it's
  safe to re-run — `seed_roles_and_permissions()` only upserts Permission/Role rows by code, it doesn't
  touch your hospitals, patients, or any other data.
- **New page**: `/system-settings` (`admin/system_settings.html`) — currency, default consultation fee,
  pharmacy low-stock reorder threshold (previously hardcoded at 10, now a real per-hospital setting via
  `Hospital.low_stock_threshold`), and an editable table of each insurance scheme's default authorization
  limit (this existed as a field with no UI until now).
- `default_consultation_fee` removed from the general Hospital Settings page — it now lives only on the
  new pricing page, so there's one place to change it, not two.
- **Honest scope note**: per-item catalog prices (ward daily rates, individual lab/radiology test prices)
  are still edited on the existing Diagnosis/Drug/Radiology/Ward catalogs page, which stays gated on
  `catalogs.manage` (so Admin can still manage catalogs generally) — not moved under `pricing.manage`.
  Fully restricting *just the price field* on those forms to the owner, while leaving the rest of catalog
  management to Admin, is a bigger, riskier change (splitting fields across existing routes/forms) than I
  wanted to make blind in this pass. The new Pricing page links to both so the owner has one place to
  *reach* everything, even though those two pages aren't yet permission-split at the field level. Say the
  word if you want that taken further.
- Drug selling prices intentionally stay per-batch (set at stock receiving, not a flat catalog price) —
  cost changes with every delivery, so a single "the price of Paracetamol" number would be wrong the
  moment a new batch comes in at a different cost. Noted on the new settings page rather than changed.

## 14. System Maintenance: platform-wide super-user + diagnostics dashboard
A new **System Maintainer** role for your own technical/support staff — not for any clinic's CEO, Admin,
or Hospital Manager. It sees every hospital across every organization on this install, and gets a
diagnostics dashboard that finds specific, well-defined data problems and can fix the fixable ones with
one click.

**No migration needed for this one** — it's new Role/Permission rows (handled by `seed.py`, existing
tables) plus route/logic changes. No new columns, no new tables.

### Access control (read this part carefully)
- New permission `system.maintain`, new role **System Maintainer** with `scope="platform"` — a level
  above the existing `organization` scope (CEO). `seed.py` — **re-run `python seed.py`** to create it.
- `User.accessible_hospital_ids()` (`app/models.py`) now returns *every* hospital for `scope="platform"`.
  This is the actual mechanism behind "sees everything" — every hospital-scoped page in the app (patients,
  billing, pharmacy, inpatient, wards) already filters through this one method, so widening it here is
  what unlocks cross-tenant visibility, rather than needing to touch every route individually.
- **There is no web UI path to grant this role, to anyone, ever** — not from the CEO account, not from
  Admin. `app/admin/routes.py:users_create()` explicitly rejects assigning any `platform`-scope role
  unless the person doing the assigning already has one (`users_list()` also filters it out of the role
  dropdown, but that's just tidiness — the real enforcement is the server-side check). The only way to
  create a System Maintainer account is the new `create_system_maintainer.py` script, run directly on
  the server with `python create_system_maintainer.py` — it prompts for credentials via the terminal
  (password via `getpass`, never shown or logged) and creates the account under a dedicated placeholder
  "System Maintenance" organization, kept separate from any real clinic's data.

### Diagnostics dashboard (`/system-maintenance`, `app/sysadmin/`)
- System info: Python/Flask/SQLAlchemy versions, DB engine, DB file size, disk space, record counts.
- Recent activity feed straight from the audit log.
- Seven data-integrity checks, each read-only until you press its Fix button:
  1. Beds marked Occupied with no active admission holding them — **fixable**
  2. Active admissions whose assigned bed isn't marked Occupied — **fixable**
  3. Admissions still Active on a visit that's already Completed/Discharged — **fixable** (closes out the
     admission and frees the bed)
  4. Stock batches with negative quantity remaining — **fixable** (corrects to zero, logs the correction
     as a stock transaction for the trail)
  5. Expired stock batches still showing units on hand — **fixable** (writes off to zero, logged)
  6. Active staff with no hospital assigned (locked out of seeing any patients) — flagged only, needs a
     human to pick the right hospital
  7. Possible duplicate patient records sharing a phone number — flagged only, merging patient records
     isn't something to automate

### Honest scope — what "online fix" does and doesn't mean here
This can't diagnose or patch an arbitrary bug the way a developer would — that's not something a web
page inside the app itself can safely do. What it does do: check for specific, well-understood kinds of
data drift (the list above) and repair exactly that, the same way a real ops runbook would. New problems
you run into in practice can be added as new checks over time — tell me what you find and I'll add it.

### Also flagged, not built
Organization-scoped pages (Diagnosis/Drug/Radiology/Insurance catalogs, Hospital Settings, Pricing &
System Settings) key off `current_user.organization_id` directly, not `accessible_hospital_ids()` — so a
System Maintainer sees their own placeholder organization's (empty) catalogs there, not a given client's.
Full "view as any organization" support would need an org-switcher across those pages, which is a bigger,
separate change I didn't want to make blind in this pass. Everything hospital-scoped (patients, visits,
billing, pharmacy, inpatient workspace, ward census) already works fully cross-tenant, since those all go
through `accessible_hospital_ids()`.

## 15. config.py: everything environment-driven, in one place
Several settings were either hardcoded in source or read from raw `os.environ` scattered across
different files. Consolidated all of it into `config.py`, following the same principle as the Pricing &
System Settings page — one place, not code changes, to adjust deployment behavior.

**No migration needed** — this is Python config only, no schema changes.

- **`config.py`** rewritten: `DevelopmentConfig` / `ProductionConfig` / `TestingConfig`, selected via
  `APP_ENV`. `python-dotenv` (already in `requirements.txt` but never actually loaded — a `.env` file
  would have silently done nothing) is now wired up properly via `load_dotenv()`.
- **New `.env.example`** — every recognized environment variable, documented, safe to copy to `.env` and
  fill in. `.env` itself is never read by git-tracked code — copy the example, don't commit the real one.
- **New `.gitignore`** — `.env`, `__pycache__`, the SQLite DB file, etc. (Didn't exist before; if you're
  not using git this one's low-stakes, just there for whenever you do.)
- **Fixed: `run.py` hardcoded `debug=True`.** This is a real security issue if ever deployed as-is — Flask's
  interactive debugger lets anyone who can reach an error page execute arbitrary code on the server. Now
  reads from `FLASK_DEBUG`/`APP_ENV` via config, defaulting to **off**. `ProductionConfig` also refuses
  to start at all if `SECRET_KEY` is still the placeholder value — loud failure instead of a silent
  security hole.
- **IntaSend payment credentials** (`app/subscription/intasend_client.py`) moved from raw `os.environ.get(...)`
  calls to `current_app.config` — same env vars, just read through one consistent path instead of a
  module reaching into the environment directly.
- **`admin/routes.py`**: the hardcoded default temp password (`"ChangeMe123!"`) for new staff accounts
  is now `DEFAULT_TEMP_PASSWORD` in config — same default value, but changeable via `.env` without
  touching code.
- **`Hospital.low_stock_threshold`** (`app/models.py`): its system-wide fallback (used until a hospital
  sets its own via Pricing & System Settings) now reads `LOW_STOCK_THRESHOLD_DEFAULT` from config instead
  of a bare module constant.
- **`HOST`/`PORT`** added — `run.py` now binds to `0.0.0.0` if you set `HOST=0.0.0.0` in `.env`, useful
  for reaching the dev server from other devices on the clinic's network (other tablets/phones), without
  editing `run.py` itself.
