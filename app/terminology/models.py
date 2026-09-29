from app.extensions import db
from app.models import now


class HptProduct(db.Model):
    """Local cache of the national Health Products & Technologies registry."""
    __tablename__ = "hpt_products"

    id = db.Column(db.Integer, primary_key=True)
    hpt_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    generic_name = db.Column(db.String(200))
    form = db.Column(db.String(60))
    strength = db.Column(db.String(60))
    atc_code = db.Column(db.String(10))
    status = db.Column(db.String(30))
    refreshed_at = db.Column(db.DateTime, default=now)
