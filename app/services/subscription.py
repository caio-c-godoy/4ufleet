from __future__ import annotations

from datetime import datetime, timedelta

from app.models import DEFAULT_TRIAL_DAYS, Tenant


def initialize_trial(
    tenant: Tenant,
    *,
    now: datetime | None = None,
    days: int = DEFAULT_TRIAL_DAYS,
) -> None:
    now = now or datetime.utcnow()
    tenant.trial_started_at = now
    tenant.trial_ends_at = now + timedelta(days=days)
    tenant.subscription_status = tenant.subscription_status or "trialing"
    tenant.subscription_provider = tenant.subscription_provider or "none"


def backfill_trial(
    tenant: Tenant,
    *,
    now: datetime | None = None,
    days: int = DEFAULT_TRIAL_DAYS,
) -> bool:
    now = now or datetime.utcnow()
    changed = False
    if not tenant.trial_started_at:
        tenant.trial_started_at = tenant.created_at or now
        changed = True
    if not tenant.trial_ends_at:
        tenant.trial_ends_at = tenant.trial_started_at + timedelta(days=days)
        changed = True
    if not tenant.subscription_status:
        tenant.subscription_status = "trialing" if tenant.trial_ends_at >= now else "active"
        changed = True
    if not tenant.subscription_provider:
        tenant.subscription_provider = "none"
        changed = True
    return changed


def get_tenant_subscription_state(
    tenant: Tenant,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    now = now or datetime.utcnow()
    trial_end = tenant.trial_ends_at
    if trial_end:
        remaining_days = (trial_end.date() - now.date()).days
    else:
        remaining_days = 0
    remaining_days = max(0, remaining_days)
    is_trial_active = bool(trial_end and trial_end >= now)

    status = tenant.subscription_status
    if not status:
        status = "trialing" if is_trial_active else "active"

    return {
        "trial_remaining_days": remaining_days,
        "is_trial_active": is_trial_active,
        "subscription_status": status,
    }
