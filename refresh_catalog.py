"""
Expands an EXISTING organization's Drug/LabTest/RadiologyTest catalog to
the full comprehensive starter lists in app/onboarding.py. Safe to re-run:
skips anything that already exists (by name for drugs, by code for lab/
radiology tests). Also removes a small number of early-prototype entries
whose naming didn't match the final comprehensive list (e.g. "ORS" ->
"Oral Rehydration Salts", "Artemether/Lumefantrine" -> the hyphenated
form), so you don't end up with near-duplicate rows.

New organizations created via seed.py or /auth/register already get the
full comprehensive list on day one — this script is only for organizations
that were created before this expansion.

Usage:
    python refresh_catalog.py <organization_id>
    python refresh_catalog.py --all     # every organization on this install
"""
import argparse

from app import create_app
from app.extensions import db
from app.models import Organization, Drug, LabTest, RadiologyTest
from app.onboarding import STARTER_DRUGS, STARTER_LAB_TESTS, STARTER_RADIOLOGY

app = create_app()

# early-prototype names/codes that got renamed in the comprehensive list —
# remove these so the org doesn't end up with both the old and new version
LEGACY_DRUG_NAMES_TO_REMOVE = ["Artemether/Lumefantrine", "ORS"]
LEGACY_LAB_CODES_TO_REMOVE = ["BS", "HIV", "STOOL"]          # superseded by BS-MALARIA, HIV-RDT, STOOLOP
LEGACY_RADIOLOGY_CODES_TO_REMOVE = ["USS-OB"]                  # superseded by USS-OB-DATE/ANOM/GROWTH


def refresh_org(org):
    removed = Drug.query.filter(
        Drug.organization_id == org.id, Drug.name.in_(LEGACY_DRUG_NAMES_TO_REMOVE)
    ).delete(synchronize_session=False)
    removed += LabTest.query.filter(
        LabTest.organization_id == org.id, LabTest.code.in_(LEGACY_LAB_CODES_TO_REMOVE)
    ).delete(synchronize_session=False)
    removed += RadiologyTest.query.filter(
        RadiologyTest.organization_id == org.id, RadiologyTest.code.in_(LEGACY_RADIOLOGY_CODES_TO_REMOVE)
    ).delete(synchronize_session=False)

    existing_drug_names = {d.name for d in Drug.query.filter_by(organization_id=org.id).all()}
    new_drugs = [
        Drug(organization_id=org.id, name=name, generic_name=generic, form=form, strength=strength, category=category)
        for name, generic, form, strength, category in STARTER_DRUGS
        if name not in existing_drug_names
    ]
    db.session.add_all(new_drugs)

    existing_lab_codes = {t.code for t in LabTest.query.filter_by(organization_id=org.id).all()}
    new_labs = [
        LabTest(organization_id=org.id, code=code, name=name, category=category, price=price)
        for code, name, category, price in STARTER_LAB_TESTS
        if code not in existing_lab_codes
    ]
    db.session.add_all(new_labs)

    existing_rad_codes = {t.code for t in RadiologyTest.query.filter_by(organization_id=org.id).all()}
    new_rads = [
        RadiologyTest(organization_id=org.id, code=code, name=name, modality=modality, price=price)
        for code, name, modality, price in STARTER_RADIOLOGY
        if code not in existing_rad_codes
    ]
    db.session.add_all(new_rads)

    db.session.commit()
    print(f"[{org.name}] removed {removed} legacy row(s), "
          f"added {len(new_drugs)} drug(s), {len(new_labs)} lab test(s), {len(new_rads)} radiology test(s)")


def run(org_id, all_orgs):
    with app.app_context():
        if all_orgs:
            orgs = Organization.query.all()
        else:
            org = Organization.query.get(org_id)
            if not org:
                print(f"No organization with id={org_id}")
                return
            orgs = [org]

        for org in orgs:
            refresh_org(org)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("organization_id", nargs="?", type=int, help="Organization ID to refresh")
    group.add_argument("--all", action="store_true", help="Refresh every organization")
    args = parser.parse_args()
    run(args.organization_id, args.all)
