"""
Generates the downloadable MediCore HMIS user manual as a PDF.

Re-run this whenever a workflow changes significantly:
    python generate_manual.py

Output: app/static/docs/MediCore_HMIS_User_Manual.pdf
"""
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, ListFlowable, ListItem, Table, TableStyle,
)
from reportlab.lib import colors

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "app", "static", "docs", "MediCore_HMIS_User_Manual.pdf")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="CoverTitle", fontSize=26, leading=32, alignment=TA_CENTER, spaceAfter=12, textColor=colors.HexColor("#065f46")))
styles.add(ParagraphStyle(name="CoverSubtitle", fontSize=13, leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#475569")))
styles.add(ParagraphStyle(name="SectionHeading", fontSize=17, leading=22, spaceBefore=6, spaceAfter=10, textColor=colors.HexColor("#065f46")))
styles.add(ParagraphStyle(name="SubHeading", fontSize=12.5, leading=16, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1e293b")))
styles.add(ParagraphStyle(name="Body", fontSize=10, leading=15, spaceAfter=6))
styles.add(ParagraphStyle(name="BulletBody", fontSize=10, leading=14))
styles.add(ParagraphStyle(name="Note", fontSize=9.5, leading=13, spaceBefore=4, spaceAfter=6, textColor=colors.HexColor("#92400e"), backColor=colors.HexColor("#fffbeb")))
styles.add(ParagraphStyle(name="TableHeader", fontSize=8.5, leading=11, textColor=colors.white, fontName="Helvetica-Bold"))
styles.add(ParagraphStyle(name="TableCell", fontSize=8.5, leading=11.5))


def h1(text):
    return Paragraph(text, styles["SectionHeading"])


def h2(text):
    return Paragraph(text, styles["SubHeading"])


def p(text):
    return Paragraph(text, styles["Body"])


def note(text):
    return Paragraph(f"<b>Note:</b> {text}", styles["Note"])


def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(item, styles["BulletBody"]), leftIndent=8) for item in items],
        bulletType="bullet", start="•", leftIndent=14, spaceBefore=2, spaceAfter=8,
    )


def numbered(items):
    flowables = []
    for i, item in enumerate(items, 1):
        flowables.append(Paragraph(f"<b>{i}.</b> {item}", styles["BulletBody"]))
    return flowables


def styled_table(data, col_widths):
    """Wraps every cell in a Paragraph so long text actually wraps within
    the column instead of overflowing into the next one."""
    wrapped = [[Paragraph(str(cell), styles["TableHeader"]) for cell in data[0]]]
    for row in data[1:]:
        wrapped.append([Paragraph(str(cell), styles["TableCell"]) for cell in row])

    table = Table(wrapped, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#065f46")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


story = []

# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------
story.append(Spacer(1, 5 * cm))
story.append(Paragraph("MediCore HMIS", styles["CoverTitle"]))
story.append(Paragraph("User Manual", styles["CoverSubtitle"]))
story.append(Spacer(1, 1 * cm))
story.append(Paragraph("A guide for every role — front desk to CEO", styles["CoverSubtitle"]))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------------
story.append(h1("Contents"))
toc_items = [
    "1. Getting Started &mdash; logging in, your first password change",
    "2. Roles &amp; Permissions &mdash; who can do what",
    "3. Registering a Patient &amp; Checking Them In",
    "4. Triage (Nurses)",
    "5. Consultation (Doctors) &mdash; history, investigations, prescriptions, diagnosis",
    "6. Pharmacy &mdash; stock and dispensing",
    "7. Lab Worklist",
    "8. Radiology Worklist",
    "9. Inpatient Care &mdash; wards, beds, transfers, vitals, nursing notes",
    "10. Billing &amp; Payments",
    "11. Medical Documents &mdash; sick offs, certificates, referrals, discharge summaries",
    "12. Administration &mdash; settings, staff, catalogs, hospitals/branches",
    "13. Dashboards",
    "14. Your Organization's Subscription",
    "15. Facility Level &amp; What It Unlocks",
    "16. Frequently Asked Questions",
]
story.append(bullets(toc_items))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 1. Getting Started
# ---------------------------------------------------------------------------
story.append(h1("1. Getting Started"))
story.append(h2("Logging in"))
story.append(p("Go to your organization's MediCore HMIS web address and sign in with the username and password your administrator gave you."))
story.append(h2("Your first password change"))
story.append(p("Every new staff account is created with a temporary password. The first time you log in, you'll be taken straight to a "
                "<b>Set Your Password</b> screen and won't be able to do anything else until you've chosen your own password. "
                "This happens automatically &mdash; you don't need to look for it."))
story.extend(numbered([
    "Enter the temporary password your administrator gave you as your Current Password.",
    "Choose a new password (at least 8 characters) and confirm it.",
    "Click <b>Set Password &amp; Continue</b>. You're taken straight to your dashboard.",
]))
story.append(note("You can change your password again any time from the <b>Change Password</b> link at the bottom of the sidebar."))
story.append(h2("Downloading this manual"))
story.append(p("A <b>User Manual</b> link sits at the bottom of the sidebar next to Change Password, on every page, for every role."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 2. Roles & Permissions
# ---------------------------------------------------------------------------
story.append(h1("2. Roles &amp; Permissions"))
story.append(p("Every account has exactly one role. A role decides which parts of the system you can see and act on. "
               "Nobody sees more than their role allows &mdash; a pharmacist can't edit hospital settings, a nurse can't dispense drugs."))

role_table_data = [
    ["Role", "What they do", "Scope"],
    ["CEO", "Oversees every hospital in the organization; manages billing, staff, catalogs, branches", "Whole organization"],
    ["Hospital Manager", "Runs one hospital day-to-day; manages that hospital's staff and settings", "One hospital"],
    ["Admin", "Manages one hospital's settings, staff, and catalogs", "One hospital"],
    ["Doctor", "Consultations, diagnoses, prescriptions, lab/radiology orders, admissions, discharges, transfers", "One hospital"],
    ["Nurse", "Registers patients, records triage, inpatient vitals and nursing notes", "One hospital"],
    ["Pharmacist", "Dispenses prescriptions, manages stock", "One hospital"],
    ["Lab Technician", "Resolves lab orders, enters results", "One hospital"],
    ["Radiologist", "Resolves radiology orders, enters findings", "One hospital"],
    ["Records Officer", "Registers patients, looks up records", "One hospital"],
    ["Billing / Insurance Clerk", "Generates bills, records payments, manages insurance claim numbers", "One hospital"],
]
role_table = styled_table(role_table_data, [3.6 * cm, 8.5 * cm, 3.4 * cm])
story.append(role_table)
story.append(Spacer(1, 8))
story.append(note("Only Hospital Manager, Admin, and CEO can create or manage staff accounts. No other role can add a new user, "
                   "even themselves &mdash; this is enforced by the system, not just hidden in the menu."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 3. Patient Registration
# ---------------------------------------------------------------------------
story.append(h1("3. Registering a Patient &amp; Checking Them In"))
story.append(p("From the <b>Patients</b> page, click <b>+ Register Patient</b>."))
story.extend(numbered([
    "Enter their bio-data: name, gender, date of birth, national ID, phone, address.",
    "Enter any known allergies and chronic conditions &mdash; this shows as a red warning banner on their record from then on, and doctors see it during every future consultation.",
    "Enter next of kin details.",
    "Select an insurance scheme if they have one, or leave as Cash / Self-pay.",
    "Choose the visit type &mdash; Outpatient, or Inpatient if your facility's level supports admissions &mdash; and a reason for the visit.",
    "Click <b>Register &amp; Check In</b>. They're immediately placed in the Triage Queue.",
]))
story.append(p("For a patient who has visited before, search for them on the Patients page, open their record, and click "
               "<b>+ New Visit / Check In</b> instead of registering them again."))
story.append(note("The <b>Inpatient</b> visit type option only appears if your facility's level allows admissions "
                   "(Level 3 and above). See Section 15 for details."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 4. Triage
# ---------------------------------------------------------------------------
story.append(h1("4. Triage (Nurses)"))
story.append(p("Your <b>Triage Queue</b> (in the sidebar) shows every patient checked in and waiting for vitals &mdash; nothing else. "
               "Open a patient from there, or from the Patients page, and click <b>+ Triage</b>."))
story.append(bullets([
    "Record temperature, pulse, blood pressure, respiratory rate, SpO2, weight, and height.",
    "Set a priority: Normal, Urgent, or Emergency. This determines their position in the doctor's queue.",
    "Add any free-text notes.",
]))
story.append(p("Once saved, the patient moves from your Triage Queue into the doctor's Consultation Queue automatically."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 5. Consultation
# ---------------------------------------------------------------------------
story.append(h1("5. Consultation (Doctors)"))
story.append(p("Your <b>Consultation Queue</b> shows patients who are triaged and ready, or already mid-consultation with you. "
               "Open a patient and click <b>+ Consultation</b> (or <b>Continue Consultation</b> if one's already started)."))
story.append(h2("History Taking"))
story.append(bullets([
    "Chief complaint and History of Presenting Illness",
    "Past Medical History, Past Surgical History, Drug History",
    "Family &amp; Social History, Review of Systems",
    "The patient's on-file allergies, chronic conditions, and triage vitals are shown right in the form so you don't have to look them up separately.",
]))
story.append(h2("Investigations"))
story.append(p("Order any number of lab tests and radiology studies directly from this form &mdash; search by name, add a clinical note if useful. "
               "Radiology search only shows what your facility's level is actually equipped for (see Section 15)."))
story.append(h2("Prescriptions"))
story.append(p("Search for a drug, add dosage, frequency, duration, quantity, and instructions. Add as many drugs as needed &mdash; each becomes "
               "its own line the pharmacist can dispense independently."))
story.append(h2("Assessment &amp; Plan"))
story.append(bullets([
    "Search and select a primary diagnosis (ICD-10) &mdash; required to complete the consultation.",
    "Click <b>+ Add another diagnosis</b> for any secondary or comorbid diagnoses (e.g. a malaria patient with known hypertension).",
    "Enter a treatment plan and, if needed, a follow-up date.",
    "For an Inpatient visit, tick <b>Admit this patient</b> to open a ward/bed selection.",
]))
story.append(h2("Saving your work"))
story.append(bullets([
    "<b>Save &amp; See Next Patient</b> &mdash; saves everything so far (including any labs/radiology/prescriptions ordered) without requiring a diagnosis yet. The visit stays open and you're free to see someone else and come back later.",
    "<b>Complete Consultation</b> &mdash; requires a diagnosis, and closes the visit (or admits the patient, if selected).",
]))
story.append(note("Re-saving a consultation replaces the list of secondary diagnoses with whatever's currently in the form &mdash; "
                   "it doesn't add to what was there before."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 6. Pharmacy
# ---------------------------------------------------------------------------
story.append(h1("6. Pharmacy"))
story.append(h2("Receiving stock"))
story.append(p("From <b>Pharmacy Stock</b>, click <b>+ Receive Stock</b>: select a drug, enter batch number, quantity, unit cost, "
               "selling price, and expiry date. A drug can have several batches with different expiry dates at once."))
story.append(h2("Dispensing"))
story.append(p("Your <b>Pharmacy Worklist</b> shows every prescription item still owed to a patient. Click <b>Dispense</b>, "
               "enter the quantity to give out, and confirm."))
story.append(bullets([
    "Stock is always taken from the batch expiring soonest first (FEFO), automatically &mdash; you don't choose the batch yourself.",
    "You can dispense less than the full amount prescribed if stock is short; the item stays open as Partially Dispensed until the rest is filled.",
    "Dispensing against zero stock is blocked with a clear error.",
]))
story.append(p("Drugs running low (under 10 units) are flagged on the Pharmacy Stock page and on your dashboard."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 7 & 8. Lab and Radiology
# ---------------------------------------------------------------------------
story.append(h1("7. Lab Worklist"))
story.append(p("Shows every lab order still pending. Update its status as you work (Ordered &rarr; Sample Collected), then click "
               "<b>Enter Result</b> to record the reading. Once saved, the result appears automatically on the patient's visit card "
               "for the ordering doctor to see."))
story.append(PageBreak())

story.append(h1("8. Radiology Worklist"))
story.append(p("Same pattern as the lab worklist: update status (Ordered &rarr; In Progress), then <b>Enter Findings</b> once the "
               "study is reported. Only studies your facility's level actually offers ever appear here in the first place."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 9. Inpatient Care
# ---------------------------------------------------------------------------
story.append(h1("9. Inpatient Care"))
story.append(h2("Wards &amp; beds"))
story.append(p("Admin/Manager/CEO sets up wards from Admin &rarr; Catalogs: name, type, total bed count, and daily rate. "
               "Individual beds can optionally be added to a ward (e.g. \"ICU-01\", \"ICU-02\") for exact bed-level tracking &mdash; "
               "a ward with beds defined requires picking a specific bed at admission time; a ward without them just tracks a "
               "headline capacity number."))
story.append(h2("Admission"))
story.append(p("Happens inside the Consultation form (Section 5) for an Inpatient visit &mdash; tick Admit this patient, choose the "
               "ward (and bed, if that ward has beds set up), and an expected discharge date."))
story.append(h2("During the stay"))
story.append(bullets([
    "<b>+ Vitals</b> (nurses) &mdash; log a new set of observations any time; every entry is kept, not just the latest.",
    "<b>+ Note</b> (nurses) &mdash; free-text ward-round notes, timestamped and attributed to whoever wrote them.",
    "<b>Transfer</b> (doctors) &mdash; move the patient to a different ward/bed mid-stay. The old bed is freed automatically and the "
    "move is logged with who did it, when, and why.",
]))
story.append(h2("Discharge"))
story.append(p("Click <b>Discharge</b> on the active admission. This closes the admission, frees the bed, and closes the visit "
               "in one action. Consider issuing a Discharge Summary document (Section 11) alongside it."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 10. Billing
# ---------------------------------------------------------------------------
story.append(h1("10. Billing &amp; Payments"))
story.append(p("Once a visit is Completed, Admitted, or Discharged, click <b>Generate Bill</b> on the visit card. This pulls in "
               "everything that's actually happened: the consultation fee, every dispensed drug at its real batch price, every "
               "resulted lab test, every reported radiology study, and &mdash; for inpatients &mdash; bed charges (days x ward daily rate). "
               "Nothing not yet done gets billed; regenerating later picks up new charges automatically as long as no payment "
               "has been recorded yet."))
story.append(h2("Recording a payment"))
story.append(bullets([
    "Open the bill and enter an amount, payment method (Cash, M-Pesa, Card, Insurance), and an optional reference.",
    "Multiple partial payments are fine &mdash; the bill's status moves Pending &rarr; Partially Paid &rarr; Paid automatically.",
    "A bill can be waived instead (e.g. fully insurance-covered) with a note explaining why.",
    "Overpayment past the balance due is rejected.",
]))
story.append(h2("Unbilled visits"))
story.append(p("The Billing Worklist has an \"Awaiting Billing\" section listing any Completed/Discharged visit nobody has "
               "billed yet, with a one-click Generate Bill button &mdash; so a finished visit never just sits there unnoticed."))
story.append(h2("Receipts"))
story.append(p("Click <b>Print Receipt</b> on any bill for a printable statement/receipt with the full charge breakdown and "
               "payment history."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 11. Medical Documents
# ---------------------------------------------------------------------------
story.append(h1("11. Medical Documents"))
story.append(p("Doctors can issue four document types from a visit card via <b>+ Document</b>:"))
story.append(bullets([
    "<b>Sick Off</b> &mdash; start/end date (days calculated automatically), diagnosis, and advice.",
    "<b>Medical Certificate</b> &mdash; general findings, recommendation, follow-up date.",
    "<b>Referral Letter</b> &mdash; receiving facility and doctor, clinical summary.",
    "<b>Discharge Summary</b> &mdash; pulls ward/admission/discharge dates automatically for an inpatient stay.",
]))
story.append(p("Every document gets a sequential reference number and a printable letterhead page (hospital header, patient "
               "details, the document body, and a signature line) with a Print / Save as PDF button."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 12. Administration
# ---------------------------------------------------------------------------
story.append(h1("12. Administration"))
story.append(h2("Hospital Settings"))
story.append(p("Name, address, contact details, and the default consultation fee used when generating bills."))
story.append(h2("Staff &amp; Roles"))
story.append(p("Add a new staff member, assign their role and hospital. A temporary password is generated for them &mdash; give it to "
               "them directly; they'll be forced to change it on first login (Section 1)."))
story.append(h2("Catalogs"))
story.append(bullets([
    "Diagnosis Codes (ICD-10) &mdash; shared system-wide reference data, the same for every organization.",
    "Drugs, Lab Tests, Radiology Tests, Insurance Schemes &mdash; specific to your organization; edit or add freely.",
    "Wards &amp; Beds &mdash; specific to your hospital.",
]))
story.append(h2("Hospitals / Branches (CEO only)"))
story.append(p("Add another branch under your organization. Each branch gets its own staff, patients, and settings, but shares "
               "your organization's drug/lab/radiology/insurance catalogs."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 13. Dashboards
# ---------------------------------------------------------------------------
story.append(h1("13. Dashboards"))
story.append(p("Your dashboard is tailored to your role &mdash; every card is a live number, not a placeholder:"))
story.append(bullets([
    "<b>CEO</b> &mdash; organization-wide totals plus a breakdown per hospital: staff, visits today, queue size, bed occupancy, revenue, outstanding balance, low-stock warnings.",
    "<b>Hospital Manager</b> &mdash; the same operational metrics scoped to your hospital(s).",
    "<b>Department staff</b> &mdash; only the cards relevant to what you actually do: a doctor sees their queue size, a pharmacist sees pending prescriptions and low stock, a billing clerk sees unpaid bills and outstanding total, and so on.",
]))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 14. Subscription
# ---------------------------------------------------------------------------
story.append(h1("14. Your Organization's Subscription"))
story.append(p("Every organization starts with a 3-day free trial. After that, a CEO, Hospital Manager, or Admin needs to "
               "choose one of two options from the <b>Subscription</b> page:"))
story.append(bullets([
    "<b>Monthly Subscription</b> &mdash; pay as you go via M-Pesa, priced by your facility's level. Access continues until the paid period ends.",
    "<b>Buy Outright</b> &mdash; a single one-time payment for permanent access at your facility's level, with no monthly bills ever again.",
]))
story.append(p("If access lapses, the whole system becomes unreachable except the Subscription page itself, so you can always "
               "get back in by paying &mdash; nothing else works until then."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 15. Facility Level
# ---------------------------------------------------------------------------
story.append(h1("15. Facility Level &amp; What It Unlocks"))
story.append(p("Your hospital's level (set when it's created) controls what services the system lets you offer, matching Kenya's "
               "facility-level framework:"))

level_table_data = [
    ["Level", "Inpatient admission", "Radiology available"],
    ["Level 1", "No", "None"],
    ["Level 2", "No", "X-Ray"],
    ["Level 3", "Yes", "X-Ray, Ultrasound"],
    ["Level 4", "Yes", "X-Ray, Ultrasound, CT"],
    ["Level 5", "Yes", "X-Ray, Ultrasound, CT, MRI, Mammography, Fluoroscopy"],
    ["Level 6", "Yes", "X-Ray, Ultrasound, CT, MRI, Mammography, Fluoroscopy"],
]
level_table = styled_table(level_table_data, [2.5 * cm, 4 * cm, 9 * cm])
story.append(level_table)
story.append(Spacer(1, 8))
story.append(note("This is enforced by the system itself, not just hidden menus &mdash; a Level 2 facility can't admit an inpatient "
                   "or find an MRI in radiology search even through a direct request. If your facility's level changes, contact "
                   "your CEO or Admin to update it in Hospital Settings."))
story.append(PageBreak())

# ---------------------------------------------------------------------------
# 16. FAQ
# ---------------------------------------------------------------------------
story.append(h1("16. Frequently Asked Questions"))

faqs = [
    ("I forgot my password.", "Ask your Hospital Manager, Admin, or CEO to reset your account &mdash; self-service password reset "
     "isn't available yet. Once reset, you'll be asked to set a new password on your next login, same as when your account "
     "was first created."),
    ("Why can't I see a patient I know was registered?", "You can only see patients registered at your own hospital. If they "
     "were registered at a different branch, ask someone at that branch to check."),
    ("Why don't I have a \"Register Patient\" button?", "Only roles with that permission (Doctor, Nurse, Records Officer, "
     "Hospital Manager, Admin, CEO) can register patients. Pharmacists, Lab Techs, Radiologists, and Billing Clerks can look "
     "patients up but not register new ones."),
    ("I can't find a specific radiology test.", "Your facility's level may not offer it &mdash; see Section 15. If you believe your "
     "hospital's level is set incorrectly, contact your CEO or Admin."),
    ("A bill's total looks wrong.", "Bills only include what's actually happened &mdash; a pending lab result or an undispensed "
     "prescription won't appear until it's resolved. Regenerating the bill after those complete will pick up the new charges, "
     "as long as no payment has been recorded against it yet."),
    ("Can I edit a consultation after finalizing it?", "Yes &mdash; reopen it from the visit card and re-save. Note that secondary "
     "diagnoses are replaced by whatever's in the form each time you save, not added to."),
]
for question, answer in faqs:
    story.append(h2(question))
    story.append(p(answer))

doc = SimpleDocTemplate(
    OUTPUT_PATH, pagesize=A4,
    leftMargin=2.2 * cm, rightMargin=2.2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    title="MediCore HMIS User Manual", author="MediCore HMIS",
)
doc.build(story)
print(f"Manual written to {OUTPUT_PATH}")
