"""Secure the seeded demo accounts before going public.

    python db/secure_accounts.py --admin-password "NewStrongPass!"            # change admin@sspp.edu only
    python db/secure_accounts.py --admin-password "NewStrongPass!" --lock-demo
        # also gives every other seeded account a random, unknown password
        # (their data stays visible; nobody can sign in with the published demo passwords)

Locked users can be given a new password later from Admin > Users > Reset password.
"""
import argparse
import os
import secrets
import sys

import oracledb
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config  # noqa: E402

ADMIN_EMAIL = "admin@sspp.edu"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--lock-demo", action="store_true")
    a = ap.parse_args()
    if len(a.admin_password) < 10:
        sys.exit("Admin password must be at least 10 characters.")

    con = oracledb.connect(user=Config.DB_USER, password=Config.DB_PASSWORD,
                           dsn=f"{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_SERVICE}")
    cur = con.cursor()
    cur.execute("UPDATE sp_users SET password_hash = :h, is_active = 1 WHERE email = :e",
                h=generate_password_hash(a.admin_password), e=ADMIN_EMAIL)
    if cur.rowcount != 1:
        sys.exit(f"{ADMIN_EMAIL} not found - run db/init_db.py first.")
    print(f"Password changed for {ADMIN_EMAIL}")

    if a.lock_demo:
        cur.execute("SELECT id FROM sp_users WHERE email <> :e", e=ADMIN_EMAIL)
        ids = [r[0] for r in cur.fetchall()]
        cur.executemany("UPDATE sp_users SET password_hash = :1 WHERE id = :2",
                        [(generate_password_hash(secrets.token_urlsafe(24)), i) for i in ids])
        print(f"Locked {len(ids)} other demo accounts with random passwords")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
