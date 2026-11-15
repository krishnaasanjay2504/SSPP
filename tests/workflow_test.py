"""End-to-end workflow test: exercises the main POST flows across roles, then cleans up its own data."""
import io
import json
import os
import sys
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["AI_MODE"] = os.environ.get("AI_MODE", "rules")
from sspp import create_app, db  # noqa: E402
from sspp.services import notify  # noqa: E402

app = create_app()
app.config["TESTING"] = True
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail if not cond else ''}")


def login(email, pwd):
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": pwd})
    assert r.status_code == 302, email
    return c


def step(name, fn):
    try:
        fn()
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        loc = next((f"{os.path.basename(f.filename)}:{f.lineno}" for f in reversed(tb) if "SSPP" in f.filename), "")
        check(name, False, f"{type(e).__name__}: {e} @ {loc}")


T = "wf-test-" + datetime.now().strftime("%H%M%S")
REG = "99CS" + datetime.now().strftime("%H%M%S")
dt = lambda d: (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%dT%H:%M")


def registration():
    c = app.test_client()
    r = c.post("/register", data={"role": "STUDENT", "full_name": "Test Student", "email": f"{T}@sspp.edu", "password": "Passw0rd!",
                                  "confirm": "Passw0rd!", "reg_no": REG, "department": "CSE", "batch_year": "2027"})
    check("student self-registration", r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM sp_users WHERE email=:e", {"e": f"{T}@sspp.edu"}))
    r = c.post("/register", data={"role": "RECRUITER", "full_name": "Test HR", "email": f"{T}@corp.com", "password": "Passw0rd!",
                                  "confirm": "Passw0rd!", "company": f"{T} Corp"})
    check("recruiter registration pending approval", db.scalar("SELECT is_approved FROM sp_users WHERE email=:e", {"e": f"{T}@corp.com"}) == 0)
    r = c.post("/login", data={"email": f"{T}@corp.com", "password": "Passw0rd!"})
    check("unapproved recruiter cannot sign in", r.status_code == 200)


def student_flow():
    c = login(f"{T}@sspp.edu", "Passw0rd!")
    r = c.post("/profile", data={"full_name": "Test Student", "reg_no": REG, "department": "CSE", "batch_year": "2027", "cgpa": "7.9",
                                 "tenth_pct": "90", "twelfth_pct": "88", "active_backlogs": "0", "history_backlogs": "0",
                                 "career_goal": "Java Developer", "summary": "Test summary", "github": "https://github.com/t"})
    check("profile save", r.status_code == 302)
    for t, title in [("SKILL", "Java"), ("SKILL", "SQL"), ("PROJECT", "Test Project"), ("EDUCATION", "B.E. CSE")]:
        c.post("/profile/items", data={"item_type": t, "title": title, "description": "Built something useful for 100 users.", "tech": "Java"})
    uid = db.scalar("SELECT id FROM sp_users WHERE email=:e", {"e": f"{T}@sspp.edu"})
    check("profile items added", db.scalar("SELECT COUNT(*) FROM sp_profile_items WHERE user_id=:u", {"u": uid}) == 4)
    r = c.post("/resumes", data={"title": "Software Developer Resume", "template_code": "classic"})
    rid = db.scalar("SELECT MAX(id) FROM sp_resumes WHERE user_id=:u", {"u": uid})
    check("resume created from profile", r.status_code == 302 and rid)
    r = c.post(f"/resumes/{rid}/edit", data={"title": "SD Resume", "template_code": "elegant", "target_role": "Java Developer",
                                             "objective": "x" * 80, "sections": ["SUMMARY", "SKILL", "PROJECT"]})
    check("resume section editing", db.scalar("SELECT template_code FROM sp_resumes WHERE id=:i", {"i": rid}) == "elegant")
    r = c.get(f"/resumes/{rid}/pdf?download=1")
    check("resume PDF export", r.status_code == 200 and r.data[:4] == b"%PDF", f"status {r.status_code}")
    c.post(f"/resumes/{rid}/duplicate")
    check("resume duplicate", db.scalar("SELECT COUNT(*) FROM sp_resumes WHERE user_id=:u", {"u": uid}) == 2)

    did = db.scalar("SELECT MIN(d.id) FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id WHERE c.name='Cognizant' AND d.status='OPEN'")
    r = c.post(f"/drives/{did}/apply", data={"resume_id": rid})
    check("eligible student applies to drive", db.scalar("SELECT COUNT(*) FROM sp_applications WHERE drive_id=:d AND student_id=:u",
                                                         {"d": did, "u": uid}) == 1)
    amz = db.scalar("SELECT d.id FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id WHERE c.name='Amazon'")
    c.post(f"/drives/{amz}/apply")
    check("ineligible student blocked (Amazon CGPA 8.0)", db.scalar("SELECT COUNT(*) FROM sp_applications WHERE drive_id=:d AND student_id=:u",
                                                                    {"d": amz, "u": uid}) == 0)
    # quiz
    qid = db.scalar("SELECT id FROM sp_quizzes WHERE title='SQL Queries'")
    c.get(f"/quizzes/{qid}")
    qs = db.query("SELECT * FROM sp_questions WHERE quiz_id=:q ORDER BY id", {"q": qid})
    form = {}
    for q in qs:
        form[f"q{q['id']}"] = q["answer"].split(",") if q["qtype"] == "MSQ" else q["answer"].split("|")[0]
    r = c.post(f"/quizzes/{qid}/submit", data=form)
    a = db.one("SELECT * FROM sp_attempts WHERE user_id=:u ORDER BY id DESC FETCH FIRST 1 ROWS ONLY", {"u": uid})
    check("quiz graded 100% when all answers correct", a and a["score"] == a["max_score"], f"{a and a['score']}/{a and a['max_score']}")
    r = c.get(r.headers["Location"])
    check("quiz result page with explanations", b"Explanation" in r.data)
    # coding
    pid = db.scalar("SELECT id FROM sp_problems WHERE title='Sum of Array'")
    r = c.post(f"/coding/{pid}/run", json={"code": "n=int(input())\nprint(sum(map(int,input().split())))", "lang": "python", "mode": "submit"})
    check("code submission accepted", r.get_json().get("verdict") == "ACCEPTED", r.get_json())
    r = c.post(f"/coding/{pid}/run", json={"code": "print(0)", "lang": "python", "mode": "run"})
    check("wrong code rejected", r.get_json().get("verdict") != "ACCEPTED")
    r = c.post(f"/coding/{pid}/run", json={"code": "while True: pass", "lang": "python", "mode": "custom", "stdin": ""})
    check("infinite loop hits time limit", r.get_json()["results"][0]["status"] == "TIME_LIMIT")
    # interview
    r = c.post("/interview/practice", data={"category": "HR", "count": "2"})
    sid = db.scalar("SELECT MAX(id) FROM sp_interview_sessions WHERE user_id=:u", {"u": uid})
    r = c.post(f"/interview/session/{sid}?i=0", data={"response": "When I was in my final year project I led a team of 4 and I built the backend. As a result we improved accuracy by 20% and I learned a lot about teamwork."})
    check("mock interview feedback", b"/10" in r.data)
    c.get(f"/interview/session/{sid}?i=1")
    c.post(f"/interview/session/{sid}?i=1", data={"response": "I am good."})
    c.get(f"/interview/session/{sid}?i=2")
    check("mock interview completes with score", db.scalar("SELECT completed FROM sp_interview_sessions WHERE id=:i", {"i": sid}) == 1)
    # forum
    c.post("/forum/ask", data={"title": f"{T} how do indexes work in Oracle?", "body": "details", "tags": "SQL, DBMS"})
    fq = db.scalar("SELECT MAX(id) FROM sp_forum_questions WHERE user_id=:u", {"u": uid})
    check("forum question posted", fq)
    # AI assistant
    r = c.post("/ai/chat", json={"message": "I want to become a Java developer. What skills am I missing?"})
    check("AI career assistant replies with skill gap", "Missing skills" in r.get_json()["html"])
    r = c.post("/ai/resume", data={"mode": "match", "resume_id": str(rid), "jd": "Looking for Java, Spring Boot, SQL, Docker and AWS skills."})
    check("AI resume ↔ JD match", b"Skill match" in r.data and b"Docker" in r.data)
    r = c.post("/ai/resume", data={"mode": "grammar", "text": "i worked on a project. it was was good"})
    check("grammar check finds issues", b"Repeated word" in r.data)
    r = c.post("/ai/resume", data={"mode": "improve", "title": "Chat app", "text": "worked on a chat app using node"})
    check("project description improver", b"Suggested bullets" in r.data)
    # notifications prefs
    c.post("/notifications/prefs", data={"offsets": ["1440", "60"], "email_on": "on"})
    check("reminder preferences saved", db.scalar("SELECT offsets FROM sp_notif_prefs WHERE user_id=:u", {"u": uid}) == "1440,60")
    c.post("/helpdesk", data={"subject": f"{T} ticket", "body": "help", "category": "Other"})
    check("helpdesk ticket raised", db.scalar("SELECT COUNT(*) FROM sp_tickets WHERE subject=:s", {"s": f"{T} ticket"}) == 1)
    return uid, did, fq


def recruiter_tpo_flow(uid, did, fq):
    rc = login("hr@cognizant.com", "Recruit@123")
    cid = db.scalar("SELECT id FROM sp_companies WHERE name='Cognizant'")
    r = rc.post("/manage/drives/new", data={"company_id": cid, "job_role": f"{T} Role", "ctc_lpa": "5", "min_cgpa": "6", "branches": ["CSE", "IT"],
                                            "batch_year": "2027", "reg_deadline": dt(5), "drive_date": dt(9), "rounds": "Aptitude,HR"})
    nd = db.scalar("SELECT id FROM sp_drives WHERE job_role=:r", {"r": f"{T} Role"})
    check("recruiter creates drive → pending approval", db.scalar("SELECT status FROM sp_drives WHERE id=:i", {"i": nd}) == "PENDING_APPROVAL")
    sc = login(f"{T}@sspp.edu", "Passw0rd!")
    check("pending drive hidden from students", sc.get(f"/drives/{nd}").status_code == 404)
    other = db.scalar("SELECT d.id FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id WHERE c.name='Zoho' AND d.status='OPEN'")
    check("recruiter cannot manage another company's drive", rc.get(f"/manage/drives/{other}").status_code == 403)

    tc = login("tpo@sspp.edu", "Tpo@12345")
    tc.post(f"/admin/approvals/drive/{nd}/approve")
    check("TPO approves drive → OPEN", db.scalar("SELECT status FROM sp_drives WHERE id=:i", {"i": nd}) == "OPEN")
    check("calendar events auto-created for drive", db.scalar("SELECT COUNT(*) FROM sp_events WHERE drive_id=:d", {"d": nd}) == 2)
    check("eligible students notified of new drive", db.scalar("SELECT COUNT(*) FROM sp_notifications WHERE link=:l", {"l": f"/drives/{nd}"}) > 0)
    rid = db.scalar("SELECT id FROM sp_users WHERE email=:e", {"e": f"{T}@corp.com"})
    tc.post(f"/admin/approvals/user/{rid}/approve")
    check("TPO approves recruiter account", db.scalar("SELECT is_approved FROM sp_users WHERE id=:i", {"i": rid}) == 1)

    aid = db.scalar("SELECT id FROM sp_applications WHERE drive_id=:d AND student_id=:u", {"d": did, "u": uid})
    rc.post(f"/manage/drives/{did}/status", data={"app_ids": [str(aid)], "new_status": "SHORTLISTED", "note": "Well done"})
    check("recruiter shortlists candidate", db.scalar("SELECT status FROM sp_applications WHERE id=:i", {"i": aid}) == "SHORTLISTED")
    rc.post(f"/manage/drives/{did}/schedule", data={"event_type": "TECH", "title": f"{T} tech round", "start_at": dt(3), "venue": "Room 1"})
    check("interview scheduled on calendar", db.scalar("SELECT COUNT(*) FROM sp_events WHERE title=:t", {"t": f"{T} tech round"}) == 1)
    rc.post(f"/manage/drives/{did}/results", data={"csv_text": f"{REG.lower()},TECH_INTERVIEW,cleared aptitude\nnobody@x.com,SELECTED"})
    check("CSV results upload by reg no", db.scalar("SELECT status FROM sp_applications WHERE id=:i", {"i": aid}) == "TECH_INTERVIEW")
    rc.post(f"/manage/drives/{did}/offer", data={"app_id": str(aid), "ctc_lpa": "4.5", "accept_by": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
                                                 "letter": (io.BytesIO(b"%PDF-1.4 test"), "offer.pdf")}, content_type="multipart/form-data")
    oid = db.scalar("SELECT id FROM sp_offers WHERE application_id=:a", {"a": aid})
    check("offer issued with letter → SELECTED", oid and db.scalar("SELECT status FROM sp_applications WHERE id=:i", {"i": aid}) == "SELECTED")
    check("offer letter downloadable by student", sc.get(f"/offers/{oid}/letter").status_code == 200)
    sc.post(f"/offers/{oid}/respond", data={"action": "ACCEPTED"})
    check("student accepts offer → PLACED", db.scalar("SELECT placement_status FROM sp_students WHERE user_id=:u", {"u": uid}) == "PLACED")
    r = rc.get(f"/manage/drives/{did}/export")
    check("applicants CSV export", r.status_code == 200 and b"Reg No" in r.data)

    fc = login("faculty@sspp.edu", "Faculty@123")
    fc.post(f"/forum/{fq}/answer", data={"body": "Indexes are B-tree structures…", "verify": "on"})
    check("faculty posts verified answer", db.scalar("SELECT is_verified FROM sp_forum_answers WHERE question_id=:q", {"q": fq}) == 1)
    a_id = db.scalar("SELECT id FROM sp_forum_answers WHERE question_id=:q", {"q": fq})
    sc.post("/forum/vote", data={"qid": fq, "target_type": "A", "target_id": a_id, "value": "1"})
    sc.post(f"/forum/accept/{a_id}")
    check("student upvotes & accepts answer", db.scalar("SELECT accepted_answer_id FROM sp_forum_questions WHERE id=:q", {"q": fq}) == a_id)
    tc.post("/calendar/new", data={"title": f"{T} training", "event_type": "TRAINING", "start_at": dt(0)[:11] + "23:59"})
    ev = db.scalar("SELECT id FROM sp_events WHERE title=:t", {"t": f"{T} training"})
    sc.post(f"/attendance/{ev}/checkin")
    check("student checks in to training", db.scalar("SELECT COUNT(*) FROM sp_event_attendance WHERE event_id=:e", {"e": ev}) == 1)
    tc.post("/jobs/new", data={"company": f"{T} Inc", "job_role": "Dev", "category": "Cloud", "job_type": "FRESHER", "work_mode": "REMOTE", "verified": "on"})
    check("TPO posts verified off-campus job", db.scalar("SELECT verified FROM sp_offcampus WHERE company=:c", {"c": f"{T} Inc"}) == 1)
    r = sc.get("/jobs?category=Cloud&mode=REMOTE")
    check("off-campus filters", f"{T} Inc".encode() in r.data)
    # reminders
    ev2 = db.insert("INSERT INTO sp_events(title,event_type,start_at) VALUES(:t,'APTITUDE',:s) RETURNING id INTO :new_id",
                    {"t": f"{T} reminder test", "s": datetime.now() + timedelta(minutes=50)})
    notify.run_reminders()
    check("1-hour reminder generated by scheduler", db.scalar("SELECT COUNT(*) FROM sp_notifications WHERE user_id=:u AND dedupe_key=:k",
                                                              {"u": uid, "k": f"ev{ev2}-60"}) == 1)
    notify.run_reminders()
    check("reminders are not duplicated", db.scalar("SELECT COUNT(*) FROM sp_notifications WHERE user_id=:u AND dedupe_key=:k",
                                                    {"u": uid, "k": f"ev{ev2}-60"}) == 1)
    return nd, rid


def cleanup(uid, nd, rid):
    db.execute("DELETE FROM sp_events WHERE title LIKE :t", {"t": f"{T}%"})
    db.execute("DELETE FROM sp_offcampus WHERE company LIKE :t", {"t": f"{T}%"})
    db.execute("DELETE FROM sp_offers WHERE student_id=:u", {"u": uid})
    db.execute("DELETE FROM sp_drives WHERE id=:d", {"d": nd})
    db.execute("DELETE FROM sp_notifications WHERE link=:l", {"l": f"/drives/{nd}"})
    db.execute("DELETE FROM sp_companies WHERE name LIKE :t", {"t": f"{T}%"})
    db.execute("DELETE FROM sp_users WHERE email LIKE :t", {"t": f"{T}%"})


state = {}
step("registration", registration)
step("student flow", lambda: state.update(zip(("uid", "did", "fq"), student_flow())))
if state:
    step("recruiter/TPO flow", lambda: state.update(zip(("nd", "rid"), recruiter_tpo_flow(state["uid"], state["did"], state["fq"]))))
    step("cleanup", lambda: cleanup(state["uid"], state.get("nd", -1), state.get("rid")))
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
