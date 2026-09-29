"""Command-line entry points (schedule these on the server).

  flask reporting-weekly     generate + submit last epi week's IDSR report (run weekly, e.g. Monday 06:00)
  flask notifications-retry  re-send pending/failed immediate notifications (run hourly)
  flask backup-run           encrypted backup + offsite copy + restore test (run daily)
  flask verify-integrity     verify the audit hash chain; exit code 1 if tampering is found
  flask dha-seed             seed roles/permissions, notifiable diseases and quality measures
"""
import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db


def register_cli(app):
    @app.cli.command("reporting-weekly")
    @with_appcontext
    def reporting_weekly():
        from app.reporting.service import run_weekly_for_all
        rows = run_weekly_for_all(current_app.config)
        db.session.commit()
        for name, label, status in rows:
            click.echo(f"{name}: {label} -> {status}")

    @app.cli.command("notifications-retry")
    @with_appcontext
    def notifications_retry():
        from app.reporting.service import retry_pending_notifications
        n = retry_pending_notifications(current_app.config)
        db.session.commit()
        click.echo(f"attempted {n}")

    @app.cli.command("backup-run")
    @with_appcontext
    def backup_run():
        from app.backup.service import run_backup_job
        r = run_backup_job(None, "scheduled")
        click.echo(f"{r.status}: {r.file_name} offsite={r.offsite_status} verify={r.verify_status or r.error}")
        raise SystemExit(0 if r.status == "OK" else 1)

    @app.cli.command("verify-integrity")
    @with_appcontext
    def verify_integrity():
        from app.compliance.service import _audit_check
        res = _audit_check()
        click.echo(f"sealed={res['sealed']} legacy={res['legacy']} altered={res['tampered']} broken_links={res['broken_links']} last_hash={res['last_hash']}")
        raise SystemExit(0 if res["ok"] else 1)

    @app.cli.command("dha-seed")
    @with_appcontext
    def dha_seed():
        from seed import seed_roles_and_permissions
        from app.reporting.service import seed_notifiable_diseases, seed_quality_measures
        seed_roles_and_permissions()
        a, b = seed_notifiable_diseases(), seed_quality_measures()
        db.session.commit()
        click.echo(f"added {a} notifiable diseases, {b} quality measures")
