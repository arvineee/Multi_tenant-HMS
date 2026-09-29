"""
Public legal pages — Privacy Policy, Terms of Service, Refund Policy.
No login required, and exempt from the trial/billing gate in
app/__init__.py (a facility whose trial has lapsed should still be able
to read what they agreed to).

IMPORTANT: the content in these templates is a starting draft, not
reviewed by a lawyer. It's written to be accurate about what the
platform actually does (data isolation, audit logging, the Kenya Data
Protection Act framework) rather than generic boilerplate, but the
business terms in it (refund conditions, liability limits) are
reasonable defaults, not confirmed decisions. Have someone qualified
review before relying on this.
"""
import datetime

from flask import Blueprint, render_template

legal_bp = Blueprint("legal", __name__, template_folder="../templates/legal")

LAST_UPDATED = "31 August 2026"


def _context():
    return {
        "last_updated": LAST_UPDATED,
        "current_year": datetime.date.today().year,
    }


@legal_bp.route("/privacy-policy")
def privacy_policy():
    return render_template("legal/privacy_policy.html", **_context())


@legal_bp.route("/terms-of-service")
def terms_of_service():
    return render_template("legal/terms_of_service.html", **_context())


@legal_bp.route("/refund-policy")
def refund_policy():
    return render_template("legal/refund_policy.html", **_context())
