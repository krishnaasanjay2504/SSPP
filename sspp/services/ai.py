"""Placement AI Assistant.

Two layers:
  1. A deterministic rule engine that always works offline (skill extraction, JD matching,
     grammar heuristics, STAR-based interview feedback, career skill-gap analysis).
  2. Optional Claude enrichment: when AI_MODE is "auto" and Anthropic credentials are available
     (ANTHROPIC_API_KEY or an `ant auth login` profile), the assistant adds a written analysis.

All feedback is developmental guidance for students - never an automated hiring decision.
"""
import logging
import os
import re

import markdown as md
from markupsafe import Markup

log = logging.getLogger("sspp.ai")
_cfg = {"AI_MODE": "auto", "AI_MODEL": "claude-opus-5-5"}
_client = None
_client_failed = False

# ---------------------------------------------------------------- skill vocabulary
SKILL_ALIASES = {
    "java": ["java", "core java", "j2ee"], "python": ["python"], "c": ["c programming", " c,", "c language"],
    "c++": ["c++", "cpp"], "javascript": ["javascript", "js", "es6"], "typescript": ["typescript"],
    "sql": ["sql", "mysql", "postgresql", "oracle sql", "pl/sql"], "dbms": ["dbms", "database management"],
    "data structures": ["data structures", "dsa"], "algorithms": ["algorithms", "algorithm"],
    "oops": ["oops", "object oriented", "object-oriented", "oop"], "html": ["html", "html5"],
    "css": ["css", "css3", "tailwind", "bootstrap"], "react": ["react", "react.js", "reactjs"],
    "angular": ["angular"], "node.js": ["node", "node.js", "express"], "spring boot": ["spring boot", "spring"],
    "django": ["django"], "flask": ["flask"], "rest api": ["rest", "restful", "rest api", "api"],
    "git": ["git", "github", "gitlab"], "linux": ["linux", "unix", "shell"], "docker": ["docker", "container"],
    "kubernetes": ["kubernetes", "k8s"], "aws": ["aws", "amazon web services", "ec2", "s3"],
    "azure": ["azure"], "gcp": ["gcp", "google cloud"], "cloud": ["cloud"],
    "ci/cd": ["ci/cd", "jenkins", "github actions", "pipeline"], "terraform": ["terraform"],
    "machine learning": ["machine learning", "ml"], "deep learning": ["deep learning", "neural network", "cnn", "lstm"],
    "nlp": ["nlp", "natural language"], "pandas": ["pandas"], "numpy": ["numpy"], "scikit-learn": ["scikit", "sklearn"],
    "tensorflow": ["tensorflow", "keras"], "pytorch": ["pytorch"], "statistics": ["statistics", "probability"],
    "power bi": ["power bi", "powerbi"], "tableau": ["tableau"], "excel": ["excel", "spreadsheet"],
    "data visualization": ["visualization", "matplotlib", "seaborn"], "selenium": ["selenium"],
    "testing": ["testing", "test cases", "junit", "pytest", "qa"], "networking": ["computer networks", "networking", "tcp/ip"],
    "operating systems": ["operating system", "os concepts"], "cybersecurity": ["security", "cybersecurity", "owasp"],
    "communication": ["communication", "presentation"], "problem solving": ["problem solving", "problem-solving"],
    "agile": ["agile", "scrum", "jira"], "microservices": ["microservices"], "system design": ["system design"],
    "mongodb": ["mongodb", "nosql"], "generative ai": ["llm", "generative ai", "genai", "prompt engineering"],
}

CAREER_PATHS = {
    "Java Developer": ["java", "oops", "data structures", "algorithms", "sql", "spring boot", "rest api", "git",
                       "microservices", "testing"],
    "Python Developer": ["python", "oops", "data structures", "sql", "django", "flask", "rest api", "git", "linux", "testing"],
    "Full Stack Developer": ["html", "css", "javascript", "react", "node.js", "rest api", "sql", "mongodb", "git", "cloud"],
    "Data Analyst": ["sql", "excel", "python", "pandas", "statistics", "power bi", "tableau", "data visualization"],
    "AI/ML Engineer": ["python", "numpy", "pandas", "statistics", "machine learning", "deep learning",
                       "scikit-learn", "tensorflow", "pytorch", "nlp", "generative ai"],
    "Cloud/DevOps Engineer": ["linux", "networking", "aws", "azure", "docker", "kubernetes", "ci/cd", "terraform", "git", "python"],
    "Cybersecurity Analyst": ["networking", "linux", "operating systems", "cybersecurity", "python", "cloud"],
    "QA / Test Engineer": ["testing", "selenium", "java", "python", "sql", "agile", "git"],
    "Business Analyst": ["excel", "sql", "power bi", "statistics", "communication", "agile"],
    "Software Developer": ["data structures", "algorithms", "oops", "java", "python", "sql", "git", "rest api",
                           "operating systems", "dbms"],
}

SKILL_TO_PATH = {  # map skills to learning path tags in sp_paths
    "java": "JAVA", "python": "PYTHON", "c": "C", "c++": "C", "sql": "SQL", "dbms": "SQL",
    "data structures": "DSA", "algorithms": "DSA", "aws": "CLOUD", "azure": "CLOUD", "cloud": "CLOUD",
    "docker": "CLOUD", "kubernetes": "CLOUD", "machine learning": "AIML", "deep learning": "AIML", "nlp": "AIML",
    "pandas": "AIML", "numpy": "AIML", "html": "WEB", "css": "WEB", "javascript": "WEB", "react": "WEB",
    "node.js": "WEB", "rest api": "WEB", "communication": "COMM",
}

ACTION_VERBS = ["Built", "Designed", "Developed", "Implemented", "Optimised", "Automated", "Led", "Engineered",
                "Deployed", "Integrated", "Reduced", "Improved", "Analysed", "Architected"]
WEAK_PHRASES = {"responsible for": "Led / Owned", "worked on": "Built / Developed", "helped": "Contributed to / Enabled",
                "did": "Delivered", "made": "Created / Built", "was involved in": "Drove", "tried to": "(remove)",
                "various": "(be specific)", "etc": "(list concrete items)", "good knowledge": "proficient in",
                "basic knowledge": "working knowledge of"}
FILLERS = ["um", "uh", "like", "basically", "actually", "you know", "kind of", "sort of"]


# ---------------------------------------------------------------- LLM layer
def configure(app):
    _cfg.update({k: app.config[k] for k in ("AI_MODE", "AI_MODEL")})


def _get_client():
    global _client, _client_failed
    if _cfg.get("AI_MODE") == "rules" or _client_failed:
        return None
    if _client is None:
        has_creds = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                                                    "ANTHROPIC_FEDERATION_RULE_ID")) or \
            os.path.isdir(os.path.join(os.path.expanduser("~"), ".config", "anthropic"))
        if not has_creds:
            _client_failed = True
            log.info("No Anthropic credentials found - AI assistant uses the built-in rule engine")
            return None
        try:
            import anthropic
            _client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / auth token / ant profile
        except Exception as e:  # SDK missing or no credentials
            log.info("Claude unavailable, using rule engine: %s", e)
            _client_failed = True
            return None
    return _client


def llm_available():
    return _get_client() is not None


def llm(system, prompt, max_tokens=2500):
    """Return Claude's text answer, or None if the model is unavailable or declines."""
    global _client_failed
    client = _get_client()
    if not client:
        return None
    try:
        import anthropic
        resp = client.beta.messages.create(
            model=_cfg["AI_MODEL"], max_tokens=max_tokens, system=system,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except anthropic.AuthenticationError:
        _client_failed = True
        log.warning("Claude authentication failed - switching to rule engine")
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable - using rule engine")
    except Exception as e:
        if "authentication" in str(e).lower():
            _client_failed = True  # credentials unusable - stop trying on every request
        log.warning("Claude call failed: %s", e)
    return None


def to_html(text):
    return Markup(md.markdown(text or "", extensions=["extra", "sane_lists"]))


SYSTEM_COACH = ("You are the Placement AI Assistant of a college training & placement cell in India. "
                "Give concise, practical, encouraging developmental guidance to students. Use short markdown "
                "sections and bullet points. Never make hiring decisions or predict selection outcomes.")


# ---------------------------------------------------------------- rule engine
def extract_skills(text):
    t = " " + (text or "").lower().replace("\n", " ") + " "
    found = set()
    for skill, aliases in SKILL_ALIASES.items():
        for a in aliases:
            pat = r"(?<![a-z0-9+#])" + re.escape(a.strip()) + r"(?![a-z0-9+#])"
            if re.search(pat, t):
                found.add(skill)
                break
    return found


def resume_text(student, items_by_type, resume=None):
    parts = [resume.get("objective") or "" if resume else "", student.get("summary") or ""]
    for typ, items in items_by_type.items():
        for i in items:
            parts += [i.get("title") or "", i.get("description") or "", i.get("tech") or "", i.get("org") or ""]
    return "\n".join(parts)


def match_jd(resume_txt, jd_txt):
    have, want = extract_skills(resume_txt), extract_skills(jd_txt)
    matched, missing = sorted(have & want), sorted(want - have)
    extra = sorted(have - want)
    score = round(100 * len(matched) / len(want)) if want else 0
    # keywords: capitalised/technical tokens from the JD that do not appear in the resume
    jd_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z+#.]{2,}", jd_txt or "")}
    stop = set("the and for with you our are will this that from have your who can able good strong work team "
               "role job candidate candidates must should experience knowledge skills using including years "
               "ability understanding required preferred plus about into across within their they".split())
    rlow = (resume_txt or "").lower()
    keywords = sorted(w for w in jd_words - stop if w not in rlow and len(w) > 3)[:25]
    suggestions = []
    if missing:
        suggestions.append(f"Add evidence for {', '.join(missing[:5])} – a project, certification or course.")
    if score < 60:
        suggestions.append("Tailor your objective to this role and mirror the JD's terminology honestly.")
    suggestions.append("Quantify impact in projects (e.g. 'reduced query time by 40%', 'served 500+ users').")
    suggestions.append("Move the most relevant projects and skills to the top for this application.")
    return {"score": score, "matched": matched, "missing": missing, "extra": extra,
            "keywords": keywords, "suggestions": suggestions}


def grammar_check(text):
    issues = []
    if not text or not text.strip():
        return issues
    for m in re.finditer(r"\b(\w+)\s+\1\b", text, re.I):
        issues.append(f"Repeated word: “{m.group(0)}”")
    if re.search(r" {2,}", text):
        issues.append("Multiple consecutive spaces found.")
    if re.search(r"(^|[.!?]\s+)[a-z]", text):
        issues.append("A sentence starts with a lowercase letter.")
    if re.search(r"\bi\b", text):
        issues.append("Lowercase “i” used as a pronoun – write “I”.")
    if re.search(r"\b(I|me|my)\b", text):
        issues.append("Resume bullets usually avoid first-person pronouns (I/me/my). Start with an action verb.")
    for weak, better in WEAK_PHRASES.items():
        if re.search(r"\b" + re.escape(weak) + r"\b", text, re.I):
            issues.append(f"Weak phrase “{weak}” → consider “{better}”.")
    if re.search(r"\b(was|were|been|being)\s+\w+ed\b", text, re.I):
        issues.append("Passive voice detected – prefer active voice (e.g. “Built X” instead of “X was built”).")
    for s in re.split(r"(?<=[.!?])\s+", text):
        if len(s.split()) > 35:
            issues.append(f"Long sentence ({len(s.split())} words) – split it for readability.")
    if not re.search(r"\d", text):
        issues.append("No numbers found – add metrics (users, %, time saved, accuracy).")
    return issues


def improve_description(title, desc, tech=""):
    skills = sorted(extract_skills(f"{title} {desc} {tech}"))
    text = desc or ""
    for weak, better in WEAK_PHRASES.items():
        first = better.split(" / ")[0]
        if not first.startswith("("):
            text = re.sub(r"\b" + re.escape(weak) + r"\b", first, text, flags=re.I)
    text = re.sub(r"\b[Ii]\b\s+", "", text).strip()
    verb = next((v for v in ACTION_VERBS if text.lower().startswith(v.lower())), None)
    tech_str = ", ".join(s.title() for s in skills) or (tech or "relevant technologies")
    bullets = [
        f"{'' if verb else 'Developed '}{text[0].upper() + text[1:] if text else title}".rstrip("."),
        f"Implemented core features using {tech_str}, following clean, modular design.",
        "Improved [performance/accuracy/usability] by [X%] through [specific optimisation].",
        "Collaborated in a team of [N] using Git; wrote tests and documentation.",
    ]
    return {"bullets": [b + "." for b in bullets], "skills": skills,
            "tips": ["Replace the [placeholders] with real numbers from your project.",
                     "Lead each bullet with a strong action verb: " + ", ".join(ACTION_VERBS[:6]) + "."]}


def career_gap(goal, student_skills_set, topic_scores=None):
    goal = goal if goal in CAREER_PATHS else "Software Developer"
    req = CAREER_PATHS[goal]
    have = {s.lower() for s in student_skills_set}
    matched = [s for s in req if s in have]
    missing = [s for s in req if s not in have]
    weak = [t for t, v in (topic_scores or {}).items() if v < 60]
    plan = []
    for i, s in enumerate(missing[:6], 1):
        plan.append({"week": f"Week {2 * i - 1}–{2 * i}", "skill": s.title(), "path": SKILL_TO_PATH.get(s)})
    return {"goal": goal, "required": req, "matched": matched, "missing": missing, "weak_topics": weak,
            "readiness": round(100 * len(matched) / len(req)), "plan": plan}


def interview_feedback(question, response, model_answer="", keywords="", category="TECHNICAL"):
    resp = (response or "").strip()
    words = resp.split()
    n = len(words)
    fb, score = [], 5.0
    if n < 25:
        fb.append("Your answer is short. Aim for 60–150 words: context, what you did, and the result.")
        score -= 2
    elif n > 250:
        fb.append("Your answer is long. Interviewers prefer crisp answers – keep to 1–2 minutes.")
        score -= 0.5
    else:
        fb.append("Good length for a spoken answer.")
        score += 1
    kws = [k.strip().lower() for k in (keywords or "").split(",") if k.strip()]
    if not kws and model_answer:
        kws = [w for w in set(re.findall(r"[a-zA-Z]{6,}", model_answer.lower()))][:10]
    hit = [k for k in kws if k in resp.lower()]
    if kws:
        cov = len(hit) / len(kws)
        score += 3 * cov
        if cov < 0.5:
            miss = [k for k in kws if k not in hit][:6]
            fb.append("Consider covering: " + ", ".join(miss) + ".")
        else:
            fb.append("You covered the key concepts well: " + ", ".join(hit[:6]) + ".")
    if category in ("HR", "BEHAVIORAL"):
        star = {"situation": r"\b(when|during|in my|at my|situation|project)\b", "action": r"\b(i (did|built|led|decided|took|created|organised|organized|handled))\b",
                "result": r"\b(result|outcome|achieved|improved|increased|reduced|learned|learnt)\b"}
        present = [k for k, p in star.items() if re.search(p, resp.lower())]
        if len(present) == 3:
            fb.append("Nice STAR structure (Situation → Action → Result).")
            score += 1
        else:
            fb.append("Use the STAR method – missing: " + ", ".join(k.title() for k in star if k not in present) + ".")
    if re.search(r"\d", resp):
        score += 0.5
    else:
        fb.append("Add specifics – numbers, technologies or outcomes make answers memorable.")
    fill = [f for f in FILLERS if re.search(r"\b" + re.escape(f) + r"\b", resp.lower())]
    if fill:
        fb.append("Avoid filler words: " + ", ".join(fill) + ".")
        score -= 0.5
    score = max(0, min(10, round(score, 1)))
    extra = llm(SYSTEM_COACH,
                f"Mock interview ({category}). Question: {question}\n\nStudent answer: {resp}\n\n"
                f"Reference points: {model_answer[:1200]}\n\nGive 3-5 bullet points of specific, kind, "
                f"developmental feedback and a stronger sample answer (max 120 words).", 1500)
    return {"score": score, "feedback": fb, "ai": extra}


FAQ = [
    (r"\b(apply|register).*(drive|company)|how.*apply", "Open **Campus Drives**, pick a drive marked *Eligible* and click **Register**. Track progress under **My Applications**."),
    (r"eligib", "Eligibility is checked automatically against CGPA, branch, batch, backlogs and 10th/12th marks. Open any drive to see a rule-by-rule breakdown."),
    (r"resume|cv", "Use **Resume Builder** to create role-specific resumes from your profile, pick a template, preview and export PDF. The **AI Resume Assistant** compares a resume to a JD."),
    (r"deadline|calendar|schedule|when", "The **Placement Calendar** shows drives, tests, interviews and deadlines. Configure reminders (7 days / 2 days / 1 day / 1 hour) under **Notifications → Preferences**."),
    (r"offer|ctc|package", "Offers appear under **Offers**, where you can accept or decline before the acceptance deadline."),
    (r"aptitude|quant|reasoning", "Practice in **Aptitude Prep** and **Quizzes** – every test shows explanations for wrong answers."),
    (r"coding|program|leetcode", "Use **Coding Practice** – problems by difficulty with test cases and instant evaluation."),
    (r"interview|hr", "Try **Mock Interview** for AI-guided practice, and browse the **Interview Question Bank** by company and role."),
]


def chat(message, context):
    """Placement chatbot / career assistant reply (returns markdown)."""
    goal_match = next((g for g in CAREER_PATHS if g.lower().split()[0] in message.lower()), None)
    sys_ctx = (f"Student context: name={context.get('name')}, dept={context.get('department')}, "
               f"CGPA={context.get('cgpa')}, goal={context.get('goal')}, skills={', '.join(sorted(context.get('skills', [])))}, "
               f"topic scores={context.get('topics')}, readiness={context.get('readiness')}.")
    ans = llm(SYSTEM_COACH + "\n" + sys_ctx, message)
    if ans:
        return ans, True
    lines = []
    if goal_match or re.search(r"skill|missing|become|career|learn|roadmap|plan", message, re.I):
        g = goal_match or context.get("goal") or "Software Developer"
        gap = career_gap(g, context.get("skills", set()), context.get("topics"))
        lines.append(f"### Skill gap for **{gap['goal']}** — readiness {gap['readiness']}%")
        lines.append("**You already have:** " + (", ".join(s.title() for s in gap["matched"]) or "—"))
        lines.append("**Missing skills:** " + (", ".join(s.title() for s in gap["missing"]) or "None 🎉"))
        if gap["weak_topics"]:
            lines.append("**Weak quiz topics:** " + ", ".join(gap["weak_topics"]))
        if gap["plan"]:
            lines.append("\n**Suggested learning plan**")
            lines += [f"- {p['week']}: {p['skill']}" + (" – see Skill Builder" if p["path"] else "") for p in gap["plan"]]
        lines.append("\nThen take a mock placement test and a mock interview to validate your readiness.")
    for pat, reply in FAQ:
        if re.search(pat, message, re.I):
            lines.append(reply)
    if not lines:
        lines.append("I can help with skill gaps (“I want to become a Java developer”), eligibility, resumes, "
                     "deadlines, offers, aptitude, coding and interview preparation. What would you like to know?")
    return "\n\n".join(lines), False
