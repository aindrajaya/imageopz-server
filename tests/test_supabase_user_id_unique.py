import pytest


def test_users_supabase_user_id_unique(client):
    from app.models import db, User
    from sqlalchemy.exc import IntegrityError

    s = db.get_session()
    try:
        u1 = User(email="u1@example.com", supabase_user_id="sb_unique_1")
        u2 = User(email="u2@example.com", supabase_user_id="sb_unique_1")
        s.add(u1)
        s.commit()

        s.add(u2)
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.close()

