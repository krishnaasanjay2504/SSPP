"""Eligibility engine: evaluates a student's record against a drive's configured rules."""
from datetime import datetime
from .. import db


def split_csv(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def student_skills(user_id):
    rows = db.query("SELECT LOWER(title) t FROM sp_profile_items WHERE user_id=:u AND item_type='SKILL'", {"u": user_id})
    return {r["t"] for r in rows}


def check(student, drive, skills=None):
    """Return dict(ok, reasons[], passed[], skill_match) for the given student row and drive row."""
    reasons, passed = [], []
    if not student:
        return {"ok": False, "reasons": ["Complete your academic profile first."], "passed": [], "skill_match": 0}

    def rule(cond, ok_msg, fail_msg):
        (passed if cond else reasons).append(ok_msg if cond else fail_msg)

    cg = float(student.get("cgpa") or 0)
    rule(cg >= float(drive.get("min_cgpa") or 0), f"CGPA {cg} ≥ {drive.get('min_cgpa') or 0}",
         f"CGPA {cg} is below the required {drive.get('min_cgpa')}")

    branches = [b.upper() for b in split_csv(drive.get("branches"))]
    if branches:
        dep = (student.get("department") or "").upper()
        rule(dep in branches, f"Branch {dep} allowed", f"Branch {dep or 'not set'} not in {', '.join(branches)}")

    if drive.get("batch_year"):
        rule(student.get("batch_year") == drive["batch_year"], f"Batch {drive['batch_year']}",
             f"Drive is for batch {drive['batch_year']} (yours: {student.get('batch_year') or 'not set'})")

    ab = int(student.get("active_backlogs") or 0)
    mab = int(drive.get("max_active_backlogs") or 0)
    rule(ab <= mab, "No active backlog issue" if mab == 0 else f"Active backlogs {ab} ≤ {mab}",
         f"{ab} active backlog(s); allowed {mab}")

    hb = int(student.get("history_backlogs") or 0)
    mhb = drive.get("max_history_backlogs")
    if mhb is not None and int(mhb) < 99:
        rule(hb <= int(mhb), f"Backlog history {hb} ≤ {mhb}", f"Backlog history {hb} exceeds {mhb}")

    for key, label in (("min_tenth", "10th"), ("min_twelfth", "12th")):
        req = float(drive.get(key) or 0)
        if req:
            val = float(student.get(key.replace("min_", "") + "_pct") or 0)
            rule(val >= req, f"{label} {val}% ≥ {req}%", f"{label} {val}% below {req}%")

    req_sk = [s.lower() for s in split_csv(drive.get("required_skills"))]
    skill_match = 100
    if req_sk:
        have = skills if skills is not None else student_skills(student["user_id"])
        hit = [s for s in req_sk if s in have]
        skill_match = round(100 * len(hit) / len(req_sk))

    return {"ok": not reasons, "reasons": reasons, "passed": passed, "skill_match": skill_match}


def registration_open(drive):
    dl = drive.get("reg_deadline")
    return drive.get("status") == "OPEN" and (dl is None or dl >= datetime.now())


def eligible_students(drive):
    """All students who satisfy the hard eligibility rules for a drive."""
    students = db.query("SELECT s.*, u.full_name, u.email FROM sp_students s JOIN sp_users u ON u.id=s.user_id "
                        "WHERE u.is_active=1")
    out = []
    for s in students:
        res = check(s, drive, skills=set())
        if res["ok"]:
            out.append(s)
    return out
