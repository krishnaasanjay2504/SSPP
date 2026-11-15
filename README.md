# Smart Student Placement Portal (SSPP)

A web-based career-readiness, campus-recruitment and off-campus opportunity platform built with **Python (Flask)** on **Oracle AI Database**.

## Quick start

```powershell
cd D:\SSPP
python -m pip install -r requirements.txt
python db\init_db.py            # create SP_ tables + demo data (use --reset to rebuild from scratch)
python run.py                   # production server (waitress) → http://localhost:5000
python run.py --dev             # development server with auto-reload
```

### Demo accounts

| Role | Email | Password |
|---|---|---|
| Student | krishnaasanjayj@gmail.com | 12345678 |
| Placement Officer (TPO) | tpo@sspp.edu | Tpo@12345 |
| Faculty / Trainer | faculty@sspp.edu | Faculty@123 |
| Recruiter (Cognizant) | hr@cognizant.com | Recruit@123 |
| Alumni / Mentor | alumni@sspp.edu | Alumni@123 |
| System Administrator | admin@sspp.edu | Admin@123 |

All 90 other seeded students use `Student@123`. **Change every password before hosting publicly.**

## Database

Connection settings live in `config.py` and can be overridden with environment variables:

| Variable | Default |
|---|---|
| `DB_USER` / `DB_PASSWORD` | SYSTEM / 12345 |
| `DB_HOST` / `DB_PORT` / `DB_SERVICE` | localhost / 1521 / FREEPDB1 |

All tables use the `SP_` prefix (`db/schema.sql`), so they never collide with objects in the SYSTEM schema. For production, create a dedicated schema user (e.g. `SSPP`) and point `DB_USER` at it rather than running as SYSTEM.

## Feature map

| Module | Where |
|---|---|
| Login, registration, 6-role RBAC | `sspp/security.py`, `views/auth.py` |
| Student profile + completeness score | `views/profile.py` |
| Resume Builder (5 templates, section editing, multiple versions, preview, PDF export, completeness score, skill & project recommendations) | `views/resume.py`, `templates/resume/` |
| AI Resume Assistant (resume ↔ JD match, missing skills/keywords, project rewriter, grammar check) | `views/assistant.py`, `services/ai.py` |
| Campus drives + automatic eligibility engine (CGPA, branch, batch, backlogs, 10th/12th, skills) | `views/drives.py`, `services/eligibility.py` |
| Application tracker (Applied → Shortlisted → Assessment → Technical → HR → Selected/Rejected) with timeline | `views/drives.py` |
| Offer management (CTC, letter upload, accept/decline, multiple offers, off-campus offers) | `views/drives.py` |
| Placement calendar (11 event types) + configurable reminders (7 d / 2 d / 1 d / 1 h) | `views/calendar.py`, `services/notify.py` |
| Notifications: in-app, email (SMTP), SMS (gateway stub) | `services/notify.py` |
| Off-campus jobs with domain/type/mode/location/experience filters, verification, saved jobs | `views/jobs.py` |
| Skill Builder – 10 learning paths, progress, badges, printable certificates | `views/learn.py` |
| Quiz engine – MCQ, multi-select, true/false, fill-in; timer; score, accuracy, time, strong/weak areas; explanations; retries | `views/learn.py` |
| Coding practice – run/submit against hidden tests (Python; C/C++/Java when compilers are installed), leaderboard, solutions | `services/code_runner.py` |
| Interview prep – question bank, company prep pages, AI mock interview with STAR feedback | `views/learn.py` |
| Aptitude & communication (GD, email writing, presentations, speech-to-text practice) | `views/learn.py` |
| Q&A forum – tags, follow, bookmarks, votes, accepted & faculty-verified answers, comments, search | `views/forum.py` |
| Alumni mentoring, student-to-alumni Q&A, placement experiences, announcements, scholarships & higher studies, helpdesk, feedback, training attendance | `views/community.py`, `views/calendar.py` |
| TPO/Admin portal – students (filters, CSV import/export), companies, drives, approvals workflow, users & roles, learning content CMS | `views/admin.py` |
| Recruiter portal – company registration, drive creation, shortlisting, scheduling, CSV result upload, offers | `views/recruiter.py`, `views/drives.py` |
| Faculty – readiness monitoring and skill-gap analysis vs recruiter demand | `views/admin.py` (`/admin/skill-gaps`) |
| Analytics – placement %, offers, highest/average/median CTC, by department, batch, company, role, gender, CTC range, skill, status; CSV export | `views/analytics.py` |

## AI features

`services/ai.py` has two layers:

1. **Built-in rule engine** (always on, no internet): skill extraction, resume ↔ JD matching, grammar/style heuristics, STAR-based interview scoring, career skill-gap plans.
2. **Claude enrichment** (optional): set `ANTHROPIC_API_KEY` and restart. Written analyses then appear alongside the rule-based results in the chat assistant, resume assistant, mock interview and career planner. Uses `claude-opus-5-5` with server-side refusal fallback; override via `AI_MODEL`. Set `AI_MODE=rules` to never call an external model.

AI feedback is presented as developmental guidance, never as a hiring decision.

## Configuration (environment variables)

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | **Set a long random value in production** (session signing) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_SENDER` | Email delivery. When unset, emails are written to `logs/email_outbox.log` |
| `SMS_ENABLED` | SMS gateway stub – messages are written to `logs/sms_outbox.log`; plug your provider into `services/notify.py` |
| `REMINDER_INTERVAL_SEC` | How often the reminder scheduler runs (default 300) |
| `CODE_EXEC_ENABLED` | `0` disables running student code on the server |
| `INSTITUTION` | College name shown in the UI and certificates |
| `HOST`, `PORT` | Server bind address (default 0.0.0.0:5000) |

## Hosting on the web

**Free hosting:** see [deploy/DEPLOY.md](deploy/DEPLOY.md). One script sets up an Oracle Cloud Always Free VM with Oracle Database 23ai Free, nginx and HTTPS.

General guidance for any server:


1. Run behind a reverse proxy (IIS with ARR, nginx or Apache) that terminates HTTPS and forwards to `python run.py` (waitress, port 5000). Install it as a Windows service with NSSM or Task Scheduler so it restarts automatically.
2. Set `SECRET_KEY`, change all demo passwords (or run `init_db.py --reset` and remove the demo users), and use a dedicated database user.
3. **Coding practice executes student code on the server.** On a public host either set `CODE_EXEC_ENABLED=0` or run the app in a locked-down container/VM.
4. Run a single app process: the reminder scheduler and the AI chat history live in-process.

## Tests

```powershell
python tests\smoke_test.py      # logs in as every role, loads every page, checks RBAC
python tests\workflow_test.py   # 46 end-to-end checks across registration, resumes, drives, approvals, offers, quizzes, coding, forum, reminders
```

## Project layout

```
config.py            settings (env-overridable)
run.py               entry point (waitress / --dev)
db/schema.sql        Oracle DDL (SP_ tables)
db/init_db.py        schema + demo data loader
db/seed_content.py   quizzes, learning paths, coding problems, interview bank
sspp/                Flask app: db layer, security, services, views (blueprints)
templates/, static/  Bootstrap 5 UI with dark mode, Chart.js and FullCalendar
tests/               smoke and workflow tests
```
