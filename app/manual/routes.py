import os

from flask import Blueprint, send_file, current_app
from flask_login import login_required

manual_bp = Blueprint("manual", __name__)


@manual_bp.route("/manual", methods=["GET"])
@login_required
def download():
    path = os.path.join(current_app.root_path, "static", "docs", "MediCore_HMIS_User_Manual.pdf")
    return send_file(path, as_attachment=True, download_name="MediCore_HMIS_User_Manual.pdf")
