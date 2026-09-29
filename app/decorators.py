from functools import wraps

from flask import abort, jsonify, request
from flask_login import current_user


def permission_required(code):
    """Blocks access unless the logged-in user's role has this permission.
    Returns JSON 403 for AJAX/fetch requests, otherwise a normal 403 page.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(code):
                if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                    return jsonify(error="You don't have permission to do that."), 403
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def any_permission_required(*codes):
    """Like permission_required, but passes if the user has ANY one of the
    given permission codes. Use this for shared spaces — e.g. the
    inpatient admission workspace, which both doctors (consultation.create)
    and nurses (triage.create) need to open, even though what each of them
    can *do* inside it is still gated per-action by the stricter code."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not any(current_user.has_permission(code) for code in codes):
                if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                    return jsonify(error="You don't have permission to do that."), 403
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def roles_required(*role_names):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role.name not in role_names:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def hospital_scoped_query(query, model):
    """Filters a SQLAlchemy query by the hospitals the current user may see.
    CEOs (organization scope) get everything in their organization;
    everyone else is restricted to their assigned hospital(s)."""
    if not current_user.is_authenticated:
        return query.filter(False)
    if current_user.role.scope == "organization":
        return query.filter(model.hospital_id.in_(current_user.accessible_hospital_ids()))
    return query.filter(model.hospital_id.in_(current_user.accessible_hospital_ids()))
