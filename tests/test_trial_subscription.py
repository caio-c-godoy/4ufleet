import os
import unittest
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Tenant, DEFAULT_TRIAL_DAYS
from app.services.subscription import (
    backfill_trial,
    get_tenant_subscription_state,
    initialize_trial,
)


class TrialSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            }
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.metadata.create_all(bind=db.engine, tables=[Tenant.__table__])

    def tearDown(self):
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, tables=[Tenant.__table__])
        self.ctx.pop()
        if self._old_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_db_url

    def test_new_tenant_initializes_trial(self):
        now = datetime(2025, 1, 1, 12, 0, 0)
        tenant = Tenant(name="Acme", slug="acme")
        initialize_trial(tenant, now=now)
        db.session.add(tenant)
        db.session.commit()

        self.assertEqual(tenant.trial_started_at, now)
        self.assertEqual(
            tenant.trial_ends_at,
            now + timedelta(days=DEFAULT_TRIAL_DAYS),
        )
        self.assertEqual(tenant.subscription_status, "trialing")
        self.assertEqual(tenant.subscription_provider, "none")

    def test_backfill_existing_tenant(self):
        created = datetime(2024, 6, 1, 8, 0, 0)
        tenant = Tenant(name="Legacy", slug="legacy", created_at=created)

        changed = backfill_trial(tenant, now=created + timedelta(days=1))
        self.assertTrue(changed)
        self.assertEqual(tenant.trial_started_at, created)
        self.assertEqual(
            tenant.trial_ends_at,
            created + timedelta(days=DEFAULT_TRIAL_DAYS),
        )
        self.assertEqual(tenant.subscription_status, "trialing")

    def test_trial_expired(self):
        now = datetime(2025, 2, 1, 12, 0, 0)
        started = now - timedelta(days=DEFAULT_TRIAL_DAYS + 5)
        tenant = Tenant(
            name="Expired",
            slug="expired",
            trial_started_at=started,
            trial_ends_at=started + timedelta(days=DEFAULT_TRIAL_DAYS),
            subscription_status="past_due",
            subscription_provider="none",
        )
        db.session.add(tenant)
        db.session.commit()

        state = get_tenant_subscription_state(tenant, now=now)
        self.assertFalse(state["is_trial_active"])
        self.assertEqual(state["trial_remaining_days"], 0)
        self.assertEqual(state["subscription_status"], "past_due")


if __name__ == "__main__":
    unittest.main()
