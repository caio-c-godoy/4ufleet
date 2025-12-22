import os
import unittest
from datetime import datetime

from app import create_app
from app.extensions import db
from app.models import Tenant, User
from app.services.billing_seats import get_seats_active


class BillingSeatsTests(unittest.TestCase):
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
        db.metadata.create_all(bind=db.engine, tables=[Tenant.__table__, User.__table__])

    def tearDown(self):
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, tables=[Tenant.__table__, User.__table__])
        self.ctx.pop()
        if self._old_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_db_url

    def _create_tenant(self, name: str, slug: str) -> Tenant:
        tenant = Tenant(name=name, slug=slug)
        db.session.add(tenant)
        db.session.commit()
        return tenant

    def _add_user(self, tenant_id: int, email: str, confirmed: bool) -> User:
        user = User(tenant_id=tenant_id, email=email, password_hash="x")
        if confirmed:
            user.email_confirmed_at = datetime.utcnow()
        db.session.add(user)
        db.session.commit()
        return user

    def test_no_active_users_returns_zero(self):
        tenant = self._create_tenant("Acme", "acme")
        self.assertEqual(get_seats_active(tenant.id), 0)

    def test_single_active_user(self):
        tenant = self._create_tenant("Beta", "beta")
        self._add_user(tenant.id, "active@beta.com", True)
        self.assertEqual(get_seats_active(tenant.id), 1)

    def test_mixed_active_inactive_users(self):
        tenant = self._create_tenant("Gamma", "gamma")
        users = []
        for i in range(3):
            users.append(
                User(
                    tenant_id=tenant.id,
                    email=f"active{i}@gamma.com",
                    password_hash="x",
                    email_confirmed_at=datetime.utcnow(),
                )
            )
        for i in range(2):
            users.append(
                User(
                    tenant_id=tenant.id,
                    email=f"inactive{i}@gamma.com",
                    password_hash="x",
                )
            )
        db.session.add_all(users)
        db.session.commit()
        self.assertEqual(get_seats_active(tenant.id), 3)

    def test_other_tenant_is_not_counted(self):
        tenant_a = self._create_tenant("Delta", "delta")
        tenant_b = self._create_tenant("Epsilon", "epsilon")
        self._add_user(tenant_a.id, "active@delta.com", True)
        self._add_user(tenant_b.id, "active@epsilon.com", True)
        self.assertEqual(get_seats_active(tenant_a.id), 1)


if __name__ == "__main__":
    unittest.main()
