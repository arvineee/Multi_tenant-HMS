"""Load WHO growth-standard LMS tables into growth_reference.

Download the official tables from WHO (Child Growth Standards 0-5 y and Growth
Reference 5-19 y) and convert each to CSV with the columns:

    indicator,sex,x,L,M,S
    wfa,M,0,0.3487,3.3464,0.14602

  indicator: wfa | lhfa | bfa | wfl | wfh | hcfa
  sex:       M | F
  x:         age in DAYS (wfa, lhfa, bfa, hcfa) or length/height in CM (wfl, wfh)

    python import_growth_reference.py who_lms.csv

The values are WHO's published data and are deliberately not embedded in this
code. Until this runs, BMI still works and z-scores stay blank.
"""
import csv
import sys

from app import create_app
from app.extensions import db
from app.records.growth import INDICATORS
from app.records.models import GrowthReference


def run(path):
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            ind, sex = row["indicator"].strip().lower(), row["sex"].strip().upper()
            if ind not in INDICATORS or sex not in ("M", "F"):
                raise SystemExit(f"row {i}: bad indicator/sex")
            x, L, M, S = (float(row[k]) for k in ("x", "L", "M", "S"))
            if M <= 0 or S <= 0:
                raise SystemExit(f"row {i}: M and S must be positive")
            existing = GrowthReference.query.filter_by(indicator=ind, sex=sex, x=x).first() or GrowthReference(indicator=ind, sex=sex, x=x, L=L, M=M, S=S)
            existing.L, existing.M, existing.S = L, M, S
            db.session.add(existing)
            n += 1
    db.session.commit()
    print(f"loaded {n} rows")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with create_app().app_context():
        run(sys.argv[1])
