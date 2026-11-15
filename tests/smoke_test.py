"""Log in as every role and request every page; report any non-2xx/3xx response or template error."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["AI_MODE"] = os.environ.get("AI_MODE", "rules")
from sspp import create_app, db  # noqa: E402

app = create_app()
app.config["TESTING"] = True
ACCOUNTS = {"STUDENT": ("student@sspp.edu", "Student@123"), "TPO": ("tpo@sspp.edu", "Tpo@12345"),
            "FACULTY": ("faculty@sspp.edu", "Faculty@123"), "RECRUITER": ("hr@cognizant.com", "Recruit@123"),
            "ALUMNI": ("alumni@sspp.edu", "Alumni@123"), "ADMIN": ("admin@sspp.edu", "Admin@123")}

ids = {
    "drive": db.scalar("SELECT MIN(d.id) FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id WHERE c.name='Cognizant'"),
    "job": db.scalar("SELECT MIN(id) FROM sp_offcampus WHERE status='APPROVED'"),
    "path": db.scalar("SELECT MIN(id) FROM sp_paths"),
    "quiz": db.scalar("SELECT MIN(id) FROM sp_quizzes"),
    "problem": db.scalar("SELECT MIN(id) FROM sp_problems"),
    "q": db.scalar("SELECT MIN(id) FROM sp_forum_questions"),
    "resume": db.scalar("SELECT MIN(r.id) FROM sp_resumes r JOIN sp_users u ON u.id=r.user_id WHERE u.email='student@sspp.edu'"),
    "student": db.scalar("SELECT id FROM sp_users WHERE email='student@sspp.edu'"),
    "event": db.scalar("SELECT MIN(id) FROM sp_events WHERE event_type='TRAINING'"),
    "attempt": db.scalar("SELECT MIN(a.id) FROM sp_attempts a JOIN sp_users u ON u.id=a.user_id WHERE u.email='student@sspp.edu'"),
    "isession": db.scalar("SELECT MIN(id) FROM sp_interview_sessions"),
}
COMMON = ["/dashboard", "/notifications", "/search?q=java", "/calendar", "/api/events?start=2020-01-01T00:00:00", "/jobs",
          f"/jobs/{ids['job']}", "/forum", f"/forum/{ids['q']}", "/forum/ask", "/announcements", "/mentoring", "/experiences",
          "/helpdesk", "/feedback", "/career-hub", "/ai/", "/attendance", f"/drives/{ids['drive']}", "/drives", "/drives?tab=past",
          "/skills", f"/skills/{ids['path']}", "/quizzes", f"/quizzes/{ids['quiz']}", "/coding", f"/coding/{ids['problem']}",
          "/interview", "/aptitude", "/communication", "/prep/Cognizant", "/api/notifications", "/account/password"]
PAGES = {
    "STUDENT": COMMON + ["/profile", "/resumes", f"/resumes/{ids['resume']}/edit", f"/resumes/{ids['resume']}/preview",
                         f"/resumes/{ids['resume']}/pdf", "/applications", "/offers", "/ai/resume", "/ai/career",
                         f"/attempts/{ids['attempt']}", f"/interview/session/{ids['isession']}", "/drives?only=eligible"],
    "TPO": COMMON + ["/tpo", "/analytics", "/analytics?dim=company", "/analytics?dim=ctc", "/analytics?dim=skill", "/analytics?dim=gender",
                     "/analytics?dim=role", "/analytics?dim=batch&export=1", "/admin/students", "/admin/students?export=1",
                     f"/admin/students/{ids['student']}", "/admin/approvals", "/admin/companies", "/admin/drives", "/admin/users",
                     "/admin/content", f"/admin/content/quiz/{ids['quiz']}", f"/admin/content/path/{ids['path']}", "/admin/skill-gaps",
                     f"/manage/drives/{ids['drive']}", f"/manage/drives/{ids['drive']}/edit", "/manage/drives/new",
                     f"/manage/drives/{ids['drive']}/export", "/jobs/new", f"/attendance/{ids['event']}", f"/resumes/{ids['resume']}/preview"],
    "FACULTY": COMMON + ["/tpo", "/analytics", "/admin/students", "/admin/skill-gaps", "/admin/content", f"/manage/drives/{ids['drive']}"],
    "RECRUITER": COMMON + ["/recruiter/", "/recruiter/company", f"/manage/drives/{ids['drive']}", "/manage/drives/new", "/jobs/new"],
    "ALUMNI": COMMON + ["/jobs/new"],
    "ADMIN": COMMON + ["/tpo", "/admin/users", "/admin/approvals", "/analytics"],
}
FORBIDDEN = {"STUDENT": ["/admin/students", "/tpo", "/recruiter/", f"/manage/drives/{ids['drive']}"],
             "RECRUITER": ["/admin/students", "/profile"], "ALUMNI": ["/admin/users", "/resumes"],
             "FACULTY": ["/admin/users", "/admin/approvals"]}

failures = 0
for role, (email, pwd) in ACCOUNTS.items():
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": pwd})
    assert r.status_code == 302, f"login failed for {role}"
    ok = 0
    for url in PAGES[role]:
        try:
            r = c.get(url)
        except Exception as e:  # template/view errors propagate in TESTING mode
            failures += 1
            import traceback
            tb = traceback.extract_tb(e.__traceback__)
            loc = next((f"{os.path.basename(f.filename)}:{f.lineno}" for f in reversed(tb) if "SSPP" in f.filename), "")
            print(f"  EXC  {role:9} {url} {type(e).__name__}: {str(e)[:160]} @ {loc}")
            continue
        if r.status_code >= 400:
            failures += 1
            body = r.get_data(as_text=True)
            m = re.search(r"<p>(.*?)</p>", body)
            print(f"  FAIL {role:9} {r.status_code} {url} {m.group(1) if m else ''}")
        else:
            ok += 1
    for url in FORBIDDEN.get(role, []):
        r = c.get(url)
        if r.status_code not in (403, 302):
            failures += 1
            print(f"  RBAC LEAK {role} can access {url} ({r.status_code})")
    print(f"{role:9} {ok}/{len(PAGES[role])} pages OK, RBAC checks {len(FORBIDDEN.get(role, []))}")
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
