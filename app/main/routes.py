import datetime

from flask import Blueprint, render_template, current_app, Response
from flask_login import current_user

from app.models import (
    Hospital, User, Visit, Admission, Ward, Bill, Payment,
    LabOrder, RadiologyOrder, PrescriptionItem, Prescription, Drug,
    LOW_STOCK_THRESHOLD,
)
from app.pharmacy.routes import _stock_on_hand

main_bp = Blueprint("main", __name__, template_folder="../templates/main")


def _day_bounds(day):
    start = datetime.datetime.combine(day, datetime.time.min)
    end = start + datetime.timedelta(days=1)
    return start, end


def _hospital_stats(hospital):
    today = datetime.date.today()
    today_start, today_end = _day_bounds(today)
    month_start = datetime.datetime.combine(today.replace(day=1), datetime.time.min)

    staff_count = User.query.filter_by(hospital_id=hospital.id, is_active=True).count()

    visits_today = Visit.query.filter(
        Visit.hospital_id == hospital.id,
        Visit.created_at >= today_start, Visit.created_at < today_end,
    ).count()
    active_visits = Visit.query.filter(
        Visit.hospital_id == hospital.id,
        Visit.status.in_(["Waiting", "Triaged", "In Consultation"]),
    ).count()
    triage_queue_count = Visit.query.filter(
        Visit.hospital_id == hospital.id, Visit.status == "Waiting",
    ).count()
    consultation_queue_count = Visit.query.filter(
        Visit.hospital_id == hospital.id, Visit.status.in_(["Triaged", "In Consultation"]),
    ).count()

    wards = Ward.query.filter_by(hospital_id=hospital.id, is_active=True).all()
    total_beds = sum(w.total_beds for w in wards)
    occupied_beds = Admission.query.filter_by(hospital_id=hospital.id, status="Active").count()

    bills = Bill.query.filter_by(hospital_id=hospital.id).all()
    outstanding_balance = sum(b.balance_due for b in bills if b.status in ("Pending", "Partially Paid"))
    pending_bills_count = sum(1 for b in bills if b.status in ("Pending", "Partially Paid"))
    billed_visit_ids = {b.visit_id for b in bills}
    unbilled_query = Visit.query.filter(
        Visit.hospital_id == hospital.id,
        Visit.status.in_(["Completed", "Discharged"]),
    )
    if billed_visit_ids:
        unbilled_query = unbilled_query.filter(~Visit.id.in_(billed_visit_ids))
    unbilled_visits_count = unbilled_query.count()

    revenue_today_rows = (
        Payment.query.join(Bill).filter(
            Bill.hospital_id == hospital.id,
            Payment.paid_at >= today_start, Payment.paid_at < today_end,
        ).with_entities(Payment.amount).all()
    )
    revenue_today = sum(a[0] for a in revenue_today_rows)

    revenue_month_rows = (
        Payment.query.join(Bill).filter(
            Bill.hospital_id == hospital.id,
            Payment.paid_at >= month_start,
        ).with_entities(Payment.amount).all()
    )
    revenue_month = sum(a[0] for a in revenue_month_rows)

    pending_lab_count = LabOrder.query.filter(
        LabOrder.hospital_id == hospital.id,
        LabOrder.status.in_(["Ordered", "Sample Collected"]),
    ).count()
    pending_radiology_count = RadiologyOrder.query.filter(
        RadiologyOrder.hospital_id == hospital.id,
        RadiologyOrder.status.in_(["Ordered", "In Progress"]),
    ).count()
    pending_prescriptions_count = PrescriptionItem.query.join(Prescription).filter(
        Prescription.hospital_id == hospital.id,
        PrescriptionItem.status.in_(["Pending", "Partially Dispensed"]),
    ).count()

    low_stock_count = sum(
        1 for d in Drug.query.filter_by(organization_id=hospital.organization_id, is_active=True).all()
        if _stock_on_hand(hospital.id, d.id) < LOW_STOCK_THRESHOLD
    )

    return {
        "hospital": hospital,
        "staff_count": staff_count,
        "visits_today": visits_today,
        "active_visits": active_visits,
        "triage_queue_count": triage_queue_count,
        "consultation_queue_count": consultation_queue_count,
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "outstanding_balance": outstanding_balance,
        "pending_bills_count": pending_bills_count,
        "unbilled_visits_count": unbilled_visits_count,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "pending_lab_count": pending_lab_count,
        "pending_radiology_count": pending_radiology_count,
        "pending_prescriptions_count": pending_prescriptions_count,
        "low_stock_count": low_stock_count,
    }


@main_bp.route("/robots.txt")
def robots_txt():
    """Served at the domain root — this is where crawlers look, not
    /static/robots.txt. Only the public landing page is meant to be
    indexed; everything else is a login-gated application screen with
    no SEO value and no business being in search results."""
    lines = [
        "User-agent: *",
        "Allow: /$",
        "Disallow: /*",
        "",
        f"Sitemap: {current_app.config['SITE_URL'].rstrip('/')}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    site_url = current_app.config["SITE_URL"].rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{site_url}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")


@main_bp.route("/")
def dashboard():
    if not current_user.is_authenticated:
        return render_template(
            "main/landing.html",
            current_year=datetime.date.today().year,
            site_url=current_app.config["SITE_URL"].rstrip("/"),
        )

    if current_user.role.scope == "platform":
        # System Maintainer — no single hospital or organization to
        # scope to by definition, so show every hospital on the install,
        # not just one organization's. Reuses the CEO dashboard template
        # (it doesn't reference any specific organization, just a list
        # of hospitals), with a flag so it can label itself accordingly.
        hospitals = Hospital.query.all()
        stats = [_hospital_stats(h) for h in hospitals]
        org_totals = {
            "hospital_count": len(hospitals),
            "staff_count": sum(s["staff_count"] for s in stats),
            "visits_today": sum(s["visits_today"] for s in stats),
            "revenue_today": sum(s["revenue_today"] for s in stats),
            "revenue_month": sum(s["revenue_month"] for s in stats),
            "outstanding_balance": sum(s["outstanding_balance"] for s in stats),
        }
        return render_template("main/dashboard_ceo.html", stats=stats, org_totals=org_totals, is_platform_view=True)

    if current_user.role.scope == "organization":
        hospitals = Hospital.query.filter_by(organization_id=current_user.organization_id).all()
        stats = [_hospital_stats(h) for h in hospitals]
        org_totals = {
            "hospital_count": len(hospitals),
            "staff_count": sum(s["staff_count"] for s in stats),
            "visits_today": sum(s["visits_today"] for s in stats),
            "revenue_today": sum(s["revenue_today"] for s in stats),
            "revenue_month": sum(s["revenue_month"] for s in stats),
            "outstanding_balance": sum(s["outstanding_balance"] for s in stats),
        }
        return render_template("main/dashboard_ceo.html", stats=stats, org_totals=org_totals)

    if current_user.role.name == "Hospital Manager":
        hospital_ids = current_user.accessible_hospital_ids()
        hospitals = Hospital.query.filter(Hospital.id.in_(hospital_ids)).all()
        stats = [_hospital_stats(h) for h in hospitals]
        return render_template("main/dashboard_manager.html", stats=stats)

    # regular department staff — tailor the stat cards to what their role acts on
    stat = _hospital_stats(current_user.hospital) if current_user.hospital else None
    return render_template("main/dashboard_staff.html", hospital=current_user.hospital, stat=stat)
