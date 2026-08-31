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

## 16. Security pass
A focused review across authentication, authorization, and a few infra basics. Several of these are
genuine vulnerabilities, not just hardening — flagged as such below.

**One needs a migration** (new `User` columns for login lockout — see below). Everything else is
Python-only, no schema change.

### Fixed: System Maintainer bounced to "trial expired" (the bug in your screenshot)
The billing/trial gate (`app/__init__.py`) applied to every authenticated user, including System
Maintainer accounts — whose placeholder organization was never meant to carry a trial at all, so it had
no `trial_ends_at` and always failed the access check. Platform-scope accounts are now exempt from this
gate entirely; no action needed on your end beyond redeploying this code — it doesn't matter what
subscription state your already-created placeholder org is in.

### Critical: cross-organization IDOR in `users_toggle_active`
Any CEO account could deactivate **any** user system-wide — a different organization's staff, or even a
System Maintainer account — just by guessing/incrementing a user ID in the request. The existing check
only restricted hospital-scoped actors (Hospital Manager/Admin); the organization-scope branch (CEO) had
no boundary check at all. Fixed with an explicit per-scope check (platform: unrestricted, organization:
same org only, narrower: same accessible hospitals only). Also added: you can no longer deactivate your
own account (avoids an admin accidentally locking themselves out).

### High: privilege escalation via role assignment in `users_create`
Anyone holding `users.manage` — which includes hospital-scoped **Hospital Manager** and **Admin**, not
just the org-wide CEO — could create a brand-new user and assign them **any** role, including CEO itself.
A compromised or malicious Hospital Manager account could mint a new CEO-level account and escalate from
single-hospital access to full organization-wide access. Fixed with a role-scope hierarchy check
(`platform > organization > hospital > department` in `app/admin/routes.py`): you can only grant a role
at or below your own scope. This one check also covers the System Maintainer case that was previously a
separate special-cased condition. The role dropdown in `users_list()` is now filtered the same way, so
Hospital Managers don't even see "CEO" as an option — cosmetic, since the real enforcement is server-side,
but avoids a confusing rejected-after-submit experience.

### High: `users_create` — mismatched hospital/organization
You could pass a `hospital_id` belonging to a *different* organization than the one being created for.
Since the new user's `organization_id` is set to the creator's own org, this left `organization_id` and
`hospital_id` pointing at two different organizations — and since hospital-scoped access is computed from
`hospital_id`, that new account would end up able to see the other organization's data. Now validated:
the hospital must belong to the acting user's own organization, regardless of their role scope. Also
added: admin-set passwords for new accounts now go through the same 8-character minimum enforced
everywhere else (previously unenforced here specifically).

### High: no brute-force protection on login
There was no account lockout, no rate limiting, no cooldown — unlimited password guesses against any
known username. Added:
- `User.failed_login_attempts` / `User.locked_until` (**new columns — migration needed**).
- `LOGIN_MAX_ATTEMPTS` (default 5) / `LOGIN_LOCKOUT_MINUTES` (default 15) in `config.py`/`.env`.
- Locked accounts are rejected *before* the password hash comparison even runs — no point doing
  deliberately-slow work on a request that's being rejected regardless.
- Failed attempts (including against usernames that don't exist at all) and lockouts are now written to
  the audit log — visible on the System Maintenance dashboard's recent-activity feed.
- New "Unlock" action on the Users page for admins to clear a legitimate lockout early, instead of making
  a locked-out staff member wait out the timer.
- **Honest limitation**: this is per-account lockout, not per-IP rate limiting. It stops repeated
  guessing against one known account, but doesn't throttle someone spraying different usernames from a
  single IP. True IP-based throttling that survives process restarts and works with multiple worker
  processes needs infrastructure outside the Flask app itself (a reverse proxy rate limiter, or
  fail2ban watching the access log) — flagging this as an infra-level recommendation rather than
  building a fragile in-process approximation.

### Medium: missing HTTP security headers
Added via `app/__init__.py` `after_request`:
- `X-Frame-Options: DENY` — clickjacking protection; this app performs real clinical/billing actions
  from buttons, so it must never be embeddable in another site's invisible iframe.
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cache-Control: no-store` on everything except `/static/` — every page here can carry patient data,
  and this app is explicitly built for shared clinic terminals (the reason the 20-minute idle session
  timeout exists at all). This stops a shared computer's disk cache from retaining a page with patient
  data on it, and stops hitting "back" after logout from resurrecting a cached page.
- `Strict-Transport-Security` once `SESSION_COOKIE_SECURE` is on (i.e. once you're actually serving over
  HTTPS).
- **Deliberately not added: Content-Security-Policy.** This codebase uses inline `onclick="..."`
  handlers and inline `<script>` blocks throughout nearly every template. A CSP strict enough to matter
  would break the UI outright; one loose enough not to (`'unsafe-inline'` for scripts) gives little real
  XSS protection while creating a false sense of security. Doing this properly means migrating inline
  handlers to `addEventListener`-based JS with a nonce-based CSP — a real, separate project, not
  something to bolt on inside a broader security pass. Flagging it rather than shipping something
  untested that could silently break pages I can't click-test from here.

### Reviewed, found clean
- No raw SQL string interpolation anywhere (`db.session.execute`/`text()` usage is limited to two
  Postgres-specific partial-index clauses in an Alembic migration, not app code) — everything else goes
  through the SQLAlchemy ORM, which parameterizes automatically.
- No `|safe` Jinja filter usage anywhere in templates — Jinja's autoescaping is intact everywhere.
- No `eval`/`exec`/`pickle`/`subprocess`/`os.system` usage anywhere in the codebase.
- The one `send_file` call (`app/manual/routes.py`, the user manual PDF) uses a fixed, hardcoded path —
  no user input reaches it, so no path-traversal risk there.
- Spot-checked every other `get_or_404`/`.query.get(...)` call site across billing, pharmacy, clinical,
  documents, inpatient, and subscription routes for the same missing-boundary-check pattern found in
  `users_toggle_active` — all of them already correctly check `accessible_hospital_ids()` or
  `organization_id` before acting. That bug appears to have been isolated to the two routes fixed above.
- Password hashing already uses Werkzeug's `generate_password_hash`/`check_password_hash` (PBKDF2), which
  is fine — no change needed.
- CSRF protection (Flask-WTF) is already active app-wide, with the only exemptions being the three routes
  that genuinely can't have a session-bound token: login, registration (no session exists yet), and the
  IntaSend payment webhook (called directly by IntaSend's servers, verified instead via the webhook
  challenge secret). All three are the correct, standard exemptions for those cases, not oversights.

## 17. Fix: System Maintainer dashboard said "no hospital assigned"
Logging in as a System Maintainer landed on the generic staff dashboard, which requires
`current_user.hospital` and shows "contact your administrator" when it's null — correct message for a
Doctor/Nurse with a missing assignment, nonsensical for a platform-wide account that's *supposed* to have
no single hospital.

- `app/main/routes.py`: added a `scope == "platform"` branch that reuses the existing CEO-style overview
  dashboard, but across **every hospital on the install** (not just one organization's) — consistent with
  `accessible_hospital_ids()` already returning everything for this scope.
- `app/templates/main/dashboard_ceo.html`: labeled "Platform Overview" instead of "Organization Overview"
  when viewed this way, and each hospital card now shows which organization it belongs to (not shown
  before, since a CEO's own view never spans more than one organization to begin with).

No migration needed — this is routing/template logic only.

## 18. Public landing page
Visiting the site root previously redirected straight to the login form — there was no public marketing
page at all. Added one, and wired it in as the actual homepage for anyone not logged in.

**No migration needed** — routing and a new template only.

- `app/main/routes.py`: `/` no longer requires login. Anonymous visitors now see the new landing page;
  anyone already logged in still lands on their normal dashboard exactly as before (nothing changed
  there).
- New `app/templates/main/landing.html`: hero, feature grid, "how it works," a security/trust section
  (given how much real work went into isolation, role scoping, audit logging, and login lockout this
  session — genuinely worth a section, not filler), and an accurate pricing table pulled straight from
  `SUBSCRIPTION_PRICING`/`ONE_TIME_PRICING`/`TRIAL_DAYS` in `models.py` — no invented numbers.
- Visual identity matches the social ad graphic from earlier (same palette, wordmark, paper-records
  motif) so the two feel like one campaign rather than two unrelated pieces.
- Register/Login buttons point at your real `auth.register_organization` / `auth.login` routes — a
  visitor can go from the homepage straight into signing up.

**On testing this yourself**: I can't render Tailwind's CDN-loaded styling in my sandbox (no internet
there), so I validated it structurally (HTML tag balance checked programmatically) but couldn't
screenshot the final styled result myself. Open it in an actual browser before considering it done —
if anything looks off, send me a screenshot the same way you have been and I'll fix it.

## 19. Meta, social sharing, and SEO for the landing page
**No migration needed** — static assets, config, and template/head changes only.

- **Config** (`config.py`, `.env.example`): new `SITE_URL` setting — the canonical domain used to build
  absolute URLs for Open Graph tags, the sitemap, and `robots.txt`. Defaults to your current PythonAnywhere
  URL; **update this in `.env` once you're on a custom domain**, or every shared link and sitemap entry
  will keep pointing at the old one.
- **Favicon**: `app/static/img/favicon.svg` (the logo mark, standalone) plus rasterized
  `favicon-16.png` / `favicon-32.png` / `apple-touch-icon.png` — modern browsers use the SVG, older ones
  and iOS home-screen bookmarks fall back to the PNGs.
- **Social share image**: `app/static/img/og-image.png` (1200×630 — the standard size Facebook/LinkedIn/
  Twitter/WhatsApp all expect). Built as an SVG first and rasterized to PNG, since most link-preview
  crawlers won't render SVG directly — same visual language as the ad graphic and landing page, so a
  shared link looks like it belongs to the same product.
- **`app/templates/main/landing.html` `<head>`**: proper `<title>`/description, canonical URL, full Open
  Graph tags (title/description/image/locale — `en_KE` for Kenya), Twitter Card tags, and a
  `SoftwareApplication` JSON-LD block (name, category, pricing range) for search engines' structured data.
- **`robots.txt` / `sitemap.xml`** (`app/main/routes.py`) — new routes, served from the domain root where
  crawlers actually look (not `/static/`, which wouldn't be found by convention). `robots.txt` allows only
  the homepage and disallows everything else — every other route in this app is a login-gated application
  screen with no SEO value, and shouldn't show up in search results at all.
- **Accessibility/semantic cleanup**: confirmed exactly one `<h1>` on the page with a clean `<h2>` hierarchy
  per section (search engines weight heading structure), and every purely decorative inline SVG icon
  (the logo mark, checkmarks, the hero graphic) now has `aria-hidden="true"` so screen readers don't
  announce meaningless icon markup.

**Worth doing once you're live**: submit `sitemap.xml` to Google Search Console, and test the share image
with Facebook's Sharing Debugger and Twitter's Card Validator — social platforms cache old previews
aggressively, so if you'd shared the link before this change, you may need to force a re-scrape there.

## 20. Logo and favicon, drawn with HTML5 Canvas
Rebuilt the favicon PNGs and added a standalone logo lockup, this time drawn with actual Canvas 2D
drawing calls (`roundRect`, `fillText`, etc.) rather than hand-written SVG — both the canvas source files
and the rendered PNGs are included.

**No migration needed** — static assets only.

- `favicon_canvas.html` / `logo_canvas.html`: open either directly in a browser to see it draw live and
  click "Download PNG" to export your own copy anytime (uses `canvas.toDataURL()`) — useful if you want
  to tweak a color or size yourself later without coming back for a new render.
- `app/static/img/favicon-16.png`, `favicon-32.png`, `apple-touch-icon.png`, `favicon-512.png` —
  regenerated from the canvas drawing, replacing the earlier SVG-rasterized versions. Same visual design,
  same file paths, so nothing else needs to change — the landing page's `<head>` already points at these
  exact filenames.
- `app/static/img/logo.png` — new: a horizontal lockup (mark + "MediCore HMIS" wordmark + tagline),
  transparent background, for anywhere you need the full logo rather than just the small icon mark —
  letterheads, printed documents, a header bar, slide decks.
- `favicon.svg` (the vector version) is untouched — Canvas can only output raster images, so the one
  format it inherently can't replace is the SVG your browser prefers when available. Both now exist:
  vector for browsers that support it, canvas-drawn PNG for everything that falls back.

## 21. Facility Operator role for small (Level 1/2) facilities
A Level 1 community unit or Level 2 dispensary is often staffed by one or two people — there's no
separate triage nurse, doctor, pharmacist, and billing clerk to split five accounts across. Added a role
that bundles the full front-line operational permission set into one account.

**No migration needed** — new role/permission rows only, via `seed.py` (re-run `python seed.py`).

- New **Facility Operator** role (`seed.py`): patient registration, triage, consultation, prescribing,
  pharmacy dispense + stock, lab order + result, radiology order + report, billing — everything needed
  to run a patient visit start to finish solo.
- Deliberately **excludes** admin-level permissions (catalogs, settings, users, pricing) — those stay on
  the CEO account created at registration, since they're setup tasks, not daily operations. The same
  person can just use both logins.
- **Restricted to Level 1/2 hospitals only**, enforced server-side in `admin/routes.py:users_create()` —
  can't be assigned to a Level 3+ facility, where separating who triages from who bills from who dispenses
  is the actual point. A UI hint on the Users page explains this before submission, not after.
- Inpatient admission and advanced radiology are already blocked for Level 1/2 by the existing
  `level_policy.py` regardless of what permissions a role has — so this role can't accidentally grant
  more than a small facility is actually allowed to do.

## 22. WhatsApp support, legal pages, and .gitignore
- **WhatsApp support** — a floating "Chat with us" button on the landing page, a "Contact Support" link
  in the authenticated sidebar, and links from all three legal pages below. Built as a `wa.me` click-to-
  chat link (`config.py`: `SUPPORT_WHATSAPP_NUMBER`, defaults to +254700459966) — no API, no credentials,
  no cost. Clicking it opens WhatsApp with a message pre-addressed to you. If you actually wanted
  *automated* backend alerts (e.g. "notify me on WhatsApp when someone registers"), that's a different,
  bigger feature needing a real WhatsApp Business API integration — say so and I'll scope that separately.
- **Three new public pages** (`app/legal/`, no login required, exempt from the trial/billing gate):
  Privacy Policy, Terms of Service, Refund Policy. Privacy Policy is grounded in the actual Kenya Data
  Protection Act, 2019 framework (data controller = each hospital, data processor = the platform) and
  what's genuinely built (data isolation, audit logs, RBAC, login lockout) rather than generic
  boilerplate.
  **I'm not a lawyer — have these reviewed before relying on them**, especially the Refund Policy's
  specific terms (7-day window on one-time purchases, etc.), which are reasonable defaults I picked, not
  confirmed business decisions.
- **Registration now requires agreeing to the Terms of Service and Privacy Policy** — a required checkbox
  on the signup form, enforced server-side too (not just a UI-only checkbox that does nothing if skipped).
- **`.gitignore`**: added `config.py` as requested. One thing worth knowing: `config.py` holds no secrets
  itself (everything sensitive comes from `.env`, which was already ignored) — gitignoring it means future
  code changes to `config.py` won't come through automatically on `git pull` on your server; you'll need to
  apply them manually each time. If that gets annoying, the alternative is un-ignoring it again (safe,
  since there's nothing sensitive in it) and relying on `.env` alone for anything that actually needs to
  stay secret.
