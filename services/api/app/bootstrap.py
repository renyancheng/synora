from app.db import Base, engine, SessionLocal
from app.domains.auth.service import ensure_bootstrap_user


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_bootstrap_user(db)
    finally:
        db.close()
