"""In-app, email and SMS notifications plus the background reminder scheduler."""
import logging
import os
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

from .. import db

log = logging.getLogger("sspp.notify")
_cfg = {}


def configure(app):
    _cfg.update(app.config)
    os.makedirs(os.path.join(app.root_path, "..", "logs"), exist_ok=True)
    _cfg["LOG_DIR"] = os.path.abspath(os.path.join(app.root_path, "..", "logs"))


def _prefs(user_id):
    return db.one("SELECT * FROM sp_notif_prefs WHERE user_id=:u", {"u": user_id}) or \
        {"offsets": "10080,2880,1440,60", "email_on": 1, "sms_on": 0, "inapp_on": 1}


def _send_email(to, subject, body):
    if not _cfg.get("SMTP_HOST"):
        _outbox("email_outbox.log", f"TO={to} | {subject} | {body}")
        return
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = _cfg["SMTP_SENDER"], to, subject
        msg.set_content(body)
        with smtplib.SMTP(_cfg["SMTP_HOST"], _cfg["SMTP_PORT"], timeout=10) as s:
            s.starttls()
            if _cfg.get("SMTP_USER"):
                s.login(_cfg["SMTP_USER"], _cfg["SMTP_PASSWORD"])
            s.send_message(msg)
    except Exception as e:  # never let mail failures break a request
        log.warning("email failed: %s", e)


def _outbox(name, line):
    try:
        with open(os.path.join(_cfg.get("LOG_DIR", "."), name), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def notify(user_ids, title, message, link=None, category="GENERAL", dedupe_key=None, email=True):
    """Create in-app notifications (and optional email/SMS) for one or more users."""
    if isinstance(user_ids, int):
        user_ids = [user_ids]
    for uid in set(user_ids):
        if dedupe_key and db.scalar("SELECT COUNT(*) FROM sp_notifications WHERE user_id=:u AND dedupe_key=:k",
                                    {"u": uid, "k": dedupe_key}):
            continue
        p = _prefs(uid)
        db.execute("INSERT INTO sp_notifications(user_id,title,message,link,category,dedupe_key) "
                   "VALUES(:u,:t,:m,:l,:c,:k)",
                   {"u": uid, "t": title[:200], "m": message[:1000], "l": link, "c": category, "k": dedupe_key})
        if email and p.get("email_on"):
            u = db.one("SELECT email, phone FROM sp_users WHERE id=:u", {"u": uid})
            if u:
                _send_email(u["email"], f"[SSPP] {title}", message)
                if p.get("sms_on") and u.get("phone"):
                    _outbox("sms_outbox.log", f"TO={u['phone']} | {title}: {message[:140]}")


def all_students():
    return [r["id"] for r in db.query("SELECT id FROM sp_users WHERE user_role='STUDENT' AND is_active=1")]


def _fmt_offset(mins):
    mins = int(mins)
    if mins >= 1440:
        d = mins // 1440
        return f"{d} day{'s' if d > 1 else ''}"
    if mins >= 60:
        return f"{mins // 60} hour{'s' if mins >= 120 else ''}"
    return f"{mins} minutes"


def run_reminders():
    """Send event and deadline reminders according to each user's configured offsets."""
    now = datetime.now()
    horizon = now + timedelta(days=8)
    events = db.query("SELECT id, title, event_type, start_at, drive_id FROM sp_events "
                      "WHERE start_at > :n AND start_at < :h", {"n": now, "h": horizon})
    students = None
    prefs = {r["user_id"]: r for r in db.query("SELECT * FROM sp_notif_prefs")}
    for ev in events:
        if ev["drive_id"]:
            audience = [r["student_id"] for r in db.query(
                "SELECT student_id FROM sp_applications WHERE drive_id=:d AND status NOT IN ('REJECTED','WITHDRAWN')",
                {"d": ev["drive_id"]})]
        else:
            students = students or all_students()
            audience = students
        mins_left = (ev["start_at"] - now).total_seconds() / 60
        for uid in audience:
            offs = [int(x) for x in (prefs.get(uid, {}).get("offsets") or "10080,2880,1440,60").split(",") if x.strip()]
            # pick the smallest offset that has already been crossed -> one reminder per threshold
            crossed = [o for o in offs if mins_left <= o]
            if not crossed:
                continue
            o = min(crossed)
            notify(uid, f"Reminder: {ev['title']}",
                   f"{ev['event_type'].replace('_', ' ').title()} starts in about {_fmt_offset(o)} "
                   f"({ev['start_at']:%d %b %Y, %I:%M %p}).",
                   link=f"/calendar?event={ev['id']}", category="REMINDER", dedupe_key=f"ev{ev['id']}-{o}")
    # registration deadlines for open drives (students who have not applied yet)
    drives = db.query("SELECT d.id, d.job_role, d.reg_deadline, c.name company FROM sp_drives d "
                      "JOIN sp_companies c ON c.id=d.company_id WHERE d.status='OPEN' AND d.reg_deadline > :n "
                      "AND d.reg_deadline < :h", {"n": now, "h": now + timedelta(days=2)})
    for d in drives:
        applied = {r["student_id"] for r in db.query("SELECT student_id FROM sp_applications WHERE drive_id=:d",
                                                     {"d": d["id"]})}
        students = students or all_students()
        for uid in students:
            if uid not in applied:
                notify(uid, f"Deadline approaching: {d['company']}",
                       f"Registration for {d['company']} – {d['job_role']} closes on "
                       f"{d['reg_deadline']:%d %b, %I:%M %p}.", link=f"/drives/{d['id']}",
                       category="DEADLINE", dedupe_key=f"dl{d['id']}", email=False)


def start_scheduler(app):
    if not app.config.get("ENABLE_SCHEDULER"):
        return
    interval = app.config.get("REMINDER_INTERVAL_SEC", 300)

    def loop():
        time.sleep(10)
        while True:
            try:
                run_reminders()
            except Exception as e:
                log.warning("reminder run failed: %s", e)
            time.sleep(interval)

    threading.Thread(target=loop, name="sspp-reminders", daemon=True).start()
