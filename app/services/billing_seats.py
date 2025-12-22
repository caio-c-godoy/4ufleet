from __future__ import annotations

from sqlalchemy import func

from app.extensions import db
from app.models import User


def get_seats_active(tenant_id: int) -> int:
    """
    Return active seats for a tenant.

    Seat rule in this project: a user is active when `User.email_confirmed_at`
    is set (login requires email confirmation).
    """
    count = (
        db.session.query(func.count(User.id))
        .filter(
            User.tenant_id == tenant_id,
            User.email_confirmed_at.isnot(None),
        )
        .scalar()
    )
    return int(count or 0)
