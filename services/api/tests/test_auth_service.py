from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.auth.service import login_user, logout_user, register_user, resolve_session_by_token
from app.models import SessionState, User


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_register_login_resolve_and_logout(self) -> None:
        user, token, expires_at = register_user(
            self.db,
            email="  New.User@Example.com ",
            password="SynoraMVP123!",
            display_name="新用户",
        )

        self.assertEqual(user.email, "new.user@example.com")
        self.assertEqual(user.display_name, "新用户")
        self.assertGreater(expires_at, datetime.now(timezone.utc))

        session = resolve_session_by_token(self.db, token)
        self.assertIsNotNone(session)
        self.assertEqual(session.user_id, user.id)

        logged_in_user, second_token, second_expires_at = login_user(
            self.db,
            "NEW.USER@example.com",
            "SynoraMVP123!",
        )
        self.assertEqual(logged_in_user.id, user.id)
        self.assertGreater(second_expires_at, datetime.now(timezone.utc))

        second_session = resolve_session_by_token(self.db, second_token)
        self.assertIsNotNone(second_session)
        self.assertEqual(second_session.user_id, user.id)

        logout_user(self.db, second_token)
        self.assertIsNone(resolve_session_by_token(self.db, second_token))
        self.assertIsNotNone(resolve_session_by_token(self.db, token))

    def test_register_rejects_duplicate_email(self) -> None:
        register_user(
            self.db,
            email="teacher@example.com",
            password="SynoraMVP123!",
            display_name="教师",
        )

        with self.assertRaisesRegex(ValueError, "该邮箱已注册"):
            register_user(
                self.db,
                email="Teacher@Example.com",
                password="Another123!",
                display_name="教师二号",
            )

    def test_login_creates_session_state(self) -> None:
        user = User(
            email="lab@example.com",
            display_name="实验室用户",
            password_hash="$2b$12$0KTJ7M6c5eV1eV0Yg9w0j.nYhUu0UDFh59OQvckV1QYV.73oGmY4W",
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        # Use register_user to generate a valid hash and stable fixture.
        self.db.delete(user)
        self.db.commit()
        registered_user, token, _ = register_user(
            self.db,
            email="lab@example.com",
            password="SynoraMVP123!",
            display_name="实验室用户",
        )

        self.assertEqual(registered_user.email, "lab@example.com")
        session_rows = self.db.scalars(
            select(SessionState).where(SessionState.user_id == registered_user.id)
        ).all()
        self.assertEqual(len(session_rows), 1)
        self.assertIsNotNone(resolve_session_by_token(self.db, token))


if __name__ == "__main__":
    unittest.main()
