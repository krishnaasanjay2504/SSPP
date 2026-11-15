"""Create the SSPP schema in Oracle and load demo data.

Usage:
    python db/init_db.py            # create tables (if missing) and seed when empty
    python db/init_db.py --reset    # DROP all SP_ tables, recreate and reseed
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

import oracledb
from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config  # noqa: E402
import seed_content as C  # noqa: E402

oracledb.defaults.fetch_lobs = False
random.seed(42)
NOW = datetime.now().replace(second=0, microsecond=0)
BATCH = NOW.year + 1 if NOW.month >= 7 else NOW.year  # current final-year batch


def conn():
    return oracledb.connect(user=Config.DB_USER, password=Config.DB_PASSWORD,
                            dsn=f"{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_SERVICE}")


def run_schema(cur):
    sql = open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8").read()
    stmts, buf = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmts.append("\n".join(buf).rstrip().rstrip(";"))
            buf = []
    for s in stmts:
        if s.strip():
            cur.execute(s)
    print(f"  schema: {len(stmts)} statements executed")


def drop_all(cur):
    cur.execute("SELECT table_name FROM user_tables WHERE table_name LIKE 'SP\\_%' ESCAPE '\\'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute(f'DROP TABLE "{t}" CASCADE CONSTRAINTS PURGE')
    print(f"  dropped {len(tables)} tables")


class Seeder:
    def __init__(self, con):
        self.con, self.cur = con, con.cursor()

    def ins(self, table, row):
        cols = list(row)
        var = self.cur.var(oracledb.NUMBER)
        self.cur.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(':' + c for c in cols)}) "
                         f"RETURNING id INTO :new_id", {**row, "new_id": var})
        v = var.getvalue()
        return int(v[0] if isinstance(v, list) else v)

    def ex(self, sql, p=None):
        self.cur.execute(sql, p or {})

    def user(self, email, pwd, name, role, **kw):
        uid = self.ins("sp_users", {"email": email, "password_hash": generate_password_hash(pwd), "full_name": name,
                                    "user_role": role, **kw})
        self.ex("INSERT INTO sp_notif_prefs(user_id) VALUES(:u)", {"u": uid})
        return uid

    # ------------------------------------------------------------------
    def run(self):
        s = self
        admin = s.user("admin@sspp.edu", "Admin@123", "System Administrator", "ADMIN", designation="IT Admin")
        tpo = s.user("tpo@sspp.edu", "Tpo@12345", "Dr. Meena Raghavan", "TPO", designation="Training & Placement Officer", phone="9840012345")
        faculty = s.user("faculty@sspp.edu", "Faculty@123", "Prof. Senthil Kumar", "FACULTY", designation="Placement Trainer – CSE")
        s.user("faculty2@sspp.edu", "Faculty@123", "Prof. Lakshmi Narayanan", "FACULTY", designation="Aptitude Trainer")

        companies = {}
        for name, ind, hq, about in [
            ("Cognizant", "IT Services", "Chennai", "Global professional services company helping businesses modernise technology and operations."),
            ("TCS", "IT Services", "Mumbai", "Tata Consultancy Services – IT services, consulting and business solutions."),
            ("Infosys", "IT Services", "Bengaluru", "Next-generation digital services and consulting."),
            ("Wipro", "IT Services", "Bengaluru", "Technology services and consulting company."),
            ("Zoho", "Product / SaaS", "Chennai", "Indian SaaS company building 55+ business applications."),
            ("Accenture", "Consulting", "Bengaluru", "Professional services in strategy, consulting, technology and operations."),
            ("Freshworks", "Product / SaaS", "Chennai", "Customer and employee engagement software."),
            ("HCLTech", "IT Services", "Noida", "Technology company with engineering and digital services."),
            ("Amazon", "Product / E-commerce", "Bengaluru", "E-commerce, cloud computing (AWS) and digital services."),
            ("L&T Technology Services", "Engineering Services", "Vadodara", "Engineering R&D services."),
        ]:
            companies[name] = s.ins("sp_companies", {"name": name, "industry": ind, "hq_location": hq, "about": about,
                                                     "website": f"https://www.{name.lower().replace(' ', '').replace('&', '')}.com",
                                                     "status": "APPROVED", "created_by": tpo})
        recruiter = s.user("hr@cognizant.com", "Recruit@123", "Priya Sharma", "RECRUITER", company_id=companies["Cognizant"],
                           designation="Campus Hiring Lead")
        s.user("talent@zoho.com", "Recruit@123", "Karthik Raja", "RECRUITER", company_id=companies["Zoho"], designation="Talent Acquisition")
        pend_c = s.ins("sp_companies", {"name": "NovaByte Labs", "industry": "AI Startup", "hq_location": "Pune", "status": "PENDING",
                                        "website": "https://novabyte.example", "about": "Early-stage AI startup."})
        pend_r = s.user("founder@novabyte.example", "Recruit@123", "Rahul Verma", "RECRUITER", company_id=pend_c, is_approved=0,
                        designation="Co-founder")
        s.ex("UPDATE sp_companies SET created_by=:u WHERE id=:c", {"u": pend_r, "c": pend_c})
        alumni = [s.user("alumni@sspp.edu", "Alumni@123", "Vignesh Subramanian", "ALUMNI", designation="SDE II, Amazon (Batch 2021)"),
                  s.user("alumni2@sspp.edu", "Alumni@123", "Divya Krishnan", "ALUMNI", designation="Data Scientist, Freshworks (Batch 2020)"),
                  s.user("alumni3@sspp.edu", "Alumni@123", "Arjun Menon", "ALUMNI", designation="Cloud Engineer, Accenture (Batch 2022)")]
        s.user("newalumni@sspp.edu", "Alumni@123", "Sneha Iyer", "ALUMNI", designation="Associate, Deloitte", is_approved=0)

        # ---------------- students
        first = ["Arun", "Divya", "Karthik", "Priya", "Rahul", "Sneha", "Vikram", "Ananya", "Harish", "Kavya", "Mohan", "Nithya",
                 "Pradeep", "Revathi", "Sanjay", "Swetha", "Tarun", "Varsha", "Yogesh", "Aishwarya", "Bala", "Deepika", "Gokul",
                 "Janani", "Kishore", "Lavanya", "Manoj", "Pooja", "Ragul", "Sowmya", "Surya", "Keerthana", "Ajay", "Bhavana",
                 "Dinesh", "Gayathri", "Hari", "Indhu", "Jagan", "Kaviya"]
        last = ["Kumar", "R", "S", "Rajan", "Natarajan", "Iyer", "Prakash", "Venkatesh", "Murugan", "Sundar", "Balaji", "Ganesh"]
        skills_pool = ["Java", "Python", "C", "C++", "SQL", "Data Structures", "HTML", "CSS", "JavaScript", "React", "Spring Boot",
                       "Django", "Flask", "AWS", "Docker", "Git", "Machine Learning", "Pandas", "Power BI", "Excel", "Linux",
                       "Node.js", "Angular", "Selenium", "Networking", "Kubernetes", "TensorFlow"]
        goals = ["Software Developer", "Java Developer", "Python Developer", "Full Stack Developer", "Data Analyst",
                 "AI/ML Engineer", "Cloud/DevOps Engineer", "QA / Test Engineer"]
        depts = ["CSE"] * 5 + ["IT"] * 3 + ["ECE"] * 3 + ["EEE"] * 2 + ["MECH"] * 2 + ["AIDS"] * 2
        students = []
        demo = s.user("student@sspp.edu", "Student@123", "Arun Kumar", "STUDENT", phone="9876543210")
        s.ex("""INSERT INTO sp_students(user_id,reg_no,department,batch_year,gender,dob,address,cgpa,tenth_pct,twelfth_pct,
                active_backlogs,history_backlogs,career_goal,interests,summary,github,linkedin,leetcode,hackerrank)
                VALUES(:u,'21CS001','CSE',:b,'Male',DATE '2004-03-14','Anna Nagar, Chennai',8.12,91.4,88.6,0,0,
                'Java Developer','Backend development, Cloud, Product companies',
                'Final-year CSE student passionate about backend development with Java and Spring Boot. Built 3 full-stack projects and completed a cloud internship.',
                'https://github.com/arunkumar','https://linkedin.com/in/arunkumar','https://leetcode.com/arunkumar','https://hackerrank.com/arunkumar')""",
             {"u": demo, "b": BATCH})
        for t, title, org, desc, st, en, url, lvl, sc, tech in [
            ("EDUCATION", "B.E. Computer Science and Engineering", "SSPP Institute of Technology", None, "2023", "2027", None, None, "CGPA 8.12", None),
            ("EDUCATION", "Higher Secondary (XII)", "Velammal Matriculation School", None, "2021", "2023", None, None, "88.6%", None),
            ("SKILL", "Java", None, None, None, None, None, "Advanced", None, None), ("SKILL", "Spring Boot", None, None, None, None, None, "Intermediate", None, None),
            ("SKILL", "SQL", None, None, None, None, None, "Advanced", None, None), ("SKILL", "Data Structures", None, None, None, None, None, "Intermediate", None, None),
            ("SKILL", "Python", None, None, None, None, None, "Intermediate", None, None), ("SKILL", "Git", None, None, None, None, None, "Intermediate", None, None),
            ("SKILL", "HTML", None, None, None, None, None, "Intermediate", None, None), ("SKILL", "AWS", None, None, None, None, None, "Beginner", None, None),
            ("PROJECT", "Smart Attendance System", "Team of 3", "Built a face-recognition based attendance system with a Spring Boot REST API and React dashboard. Reduced manual attendance time by 80% for 4 classes.", "Jan 2026", "Apr 2026", "https://github.com/arunkumar/smart-attendance", None, None, "Java, Spring Boot, OpenCV, MySQL, React"),
            ("PROJECT", "Placement Analytics Dashboard", "Mini project", "worked on a dashboard to show placement stats. i made charts and the backend.", "Aug 2025", "Oct 2025", None, None, None, "Python, Flask, Chart.js"),
            ("INTERNSHIP", "Cloud Intern", "Accenture", "Automated EC2 provisioning scripts and set up CloudWatch alarms for a client environment.", "May 2026", "Jun 2026", None, None, None, None),
            ("CERTIFICATION", "Oracle Certified Associate, Java SE", "Oracle", None, "2025", None, "https://education.oracle.com", None, None, None),
            ("CERTIFICATION", "AWS Cloud Practitioner", "Amazon Web Services", None, "2026", None, None, None, None, None),
            ("ACHIEVEMENT", "Winner – Intra-college Coding Contest", None, "Ranked 1st among 220 participants.", "2025", None, None, None, None, None),
            ("HACKATHON", "Smart India Hackathon", "Govt. of India", "Built an agri-supply-chain tracker prototype.", "2025", None, None, None, "Finalist", None),
            ("WORKSHOP", "Generative AI Bootcamp", "IIT Madras Pravartak", None, "2026", None, None, None, None, None),
            ("ACTIVITY", "Technical Lead", "Coding Club", "Organised weekly contests for 150+ members.", "2025", "Present", None, None, None, None),
            ("LANGUAGE", "English", None, None, None, None, None, "Fluent", None, None), ("LANGUAGE", "Tamil", None, None, None, None, None, "Native", None, None),
        ]:
            s.ex("""INSERT INTO sp_profile_items(user_id,item_type,title,org,description,start_label,end_label,url,item_level,score,tech)
                    VALUES(:u,:t,:ti,:o,:d,:s,:e,:url,:l,:sc,:te)""",
                 {"u": demo, "t": t, "ti": title, "o": org, "d": desc, "s": st, "e": en, "url": url, "l": lvl, "sc": sc, "te": tech})
        allsec = "SUMMARY,EDUCATION,SKILL,PROJECT,INTERNSHIP,CERTIFICATION,ACHIEVEMENT,HACKATHON,WORKSHOP,ACTIVITY,LANGUAGE,LINKS"
        r1 = s.ins("sp_resumes", {"user_id": demo, "title": "Software Developer Resume", "template_code": "modern", "target_role": "Java Developer",
                                  "objective": "Final-year CSE student skilled in Java, Spring Boot and SQL, seeking a software developer role to build scalable backend systems and grow into a cloud-native engineer.",
                                  "sections": allsec})
        s.ins("sp_resumes", {"user_id": demo, "title": "Cloud/DevOps Resume", "template_code": "teal", "target_role": "Cloud/DevOps Engineer",
                             "objective": "Aspiring cloud engineer with AWS internship experience and certification, eager to automate infrastructure and CI/CD pipelines.",
                             "sections": "SUMMARY,EDUCATION,SKILL,INTERNSHIP,CERTIFICATION,PROJECT,LINKS"})
        students.append({"id": demo, "dept": "CSE", "cgpa": 8.12, "ab": 0, "batch": BATCH, "gender": "Male"})

        for i in range(90):
            fn, ln = first[i % len(first)], random.choice(last)
            dept = random.choice(depts)
            batch = BATCH if i < 75 else BATCH - 1
            cg = round(min(9.8, max(5.4, random.gauss(7.4, 0.9))), 2)
            ab = 0 if random.random() > 0.12 else random.randint(1, 2)
            gender = "Female" if fn in ("Divya", "Priya", "Sneha", "Ananya", "Kavya", "Nithya", "Revathi", "Swetha", "Varsha", "Aishwarya",
                                         "Deepika", "Janani", "Lavanya", "Pooja", "Sowmya", "Keerthana", "Bhavana", "Gayathri", "Indhu", "Kaviya") else "Male"
            uid = s.user(f"{fn.lower()}.{ln.lower()}{i}@sspp.edu", "Student@123", f"{fn} {ln}", "STUDENT")
            code = {"CSE": "CS", "IT": "IT", "ECE": "EC", "EEE": "EE", "MECH": "ME", "AIDS": "AD"}[dept]
            s.ex("""INSERT INTO sp_students(user_id,reg_no,department,batch_year,gender,cgpa,tenth_pct,twelfth_pct,active_backlogs,
                    history_backlogs,career_goal,github,linkedin) VALUES(:u,:r,:d,:b,:g,:c,:t,:tw,:ab,:hb,:goal,:gh,:li)""",
                 {"u": uid, "r": f"{str(batch - 4)[2:]}{code}{100 + i:03d}", "d": dept, "b": batch, "g": gender, "c": cg,
                  "t": round(random.uniform(70, 98), 1), "tw": round(random.uniform(65, 96), 1), "ab": ab, "hb": ab + random.choice([0, 0, 1]),
                  "goal": random.choice(goals), "gh": f"https://github.com/{fn.lower()}{i}" if random.random() > .3 else None,
                  "li": f"https://linkedin.com/in/{fn.lower()}{i}" if random.random() > .4 else None})
            for sk in random.sample(skills_pool, random.randint(3, 8)):
                s.ex("INSERT INTO sp_profile_items(user_id,item_type,title,item_level) VALUES(:u,'SKILL',:t,:l)",
                     {"u": uid, "t": sk, "l": random.choice(["Beginner", "Intermediate", "Advanced"])})
            s.ex("INSERT INTO sp_profile_items(user_id,item_type,title,org,start_label,end_label,score) VALUES(:u,'EDUCATION',:t,"
                 "'SSPP Institute of Technology',:s,:e,:sc)", {"u": uid, "t": f"B.E./B.Tech {dept}", "s": str(batch - 4), "e": str(batch), "sc": f"CGPA {cg}"})
            if random.random() > .35:
                s.ex("INSERT INTO sp_profile_items(user_id,item_type,title,description,tech) VALUES(:u,'PROJECT',:t,:d,:te)",
                     {"u": uid, "t": random.choice(["E-commerce Website", "Chat Application", "Weather Dashboard", "Library System",
                                                     "IoT Smart Home", "Sentiment Analyzer", "Expense Tracker"]),
                      "d": "Developed the application end-to-end with authentication and reporting.", "te": random.choice(skills_pool)})
            if random.random() > .45:
                s.ins("sp_resumes", {"user_id": uid, "title": "Software Developer Resume", "template_code": random.choice(["modern", "classic", "minimal"]),
                                     "target_role": random.choice(goals), "objective": "Motivated graduate seeking a software role.", "sections": allsec})
            students.append({"id": uid, "dept": dept, "cgpa": cg, "ab": ab, "batch": batch, "gender": gender})

        # ---------------- drives
        def drive(company, role, ctc, cgpa, branches, rounds, status, dl_days, dd_days, skills, locs, jd, jt="FULL_TIME", created=tpo, batch=BATCH):
            return s.ins("sp_drives", {"company_id": companies.get(company, company), "job_role": role, "ctc_lpa": ctc, "min_cgpa": cgpa,
                                       "branches": branches, "rounds": rounds, "status": status, "batch_year": batch,
                                       "reg_deadline": NOW + timedelta(days=dl_days), "drive_date": NOW + timedelta(days=dd_days, hours=2),
                                       "required_skills": skills, "locations": locs, "job_desc": jd, "job_type": jt,
                                       "max_active_backlogs": 0, "max_history_backlogs": 99, "min_tenth": 60, "min_twelfth": 60,
                                       "venue": "Main Auditorium, Block A", "created_by": created})
        std_rounds = "Aptitude Test,Coding Test,Technical Interview,HR Interview"
        d_cog = drive("Cognizant", "Programmer Analyst Trainee", 4.5, 6.5, "CSE,IT,ECE", std_rounds, "OPEN", 5, 10, "Java,SQL,Data Structures",
                      "Chennai,Bengaluru,Hyderabad",
                      "Cognizant GenC hiring for the Programmer Analyst Trainee role.\n\nResponsibilities:\n- Develop and test application modules in Java/.NET\n- Write SQL queries and work with relational databases\n- Participate in agile ceremonies and code reviews\n\nTraining: 3-month GenC training programme before project allocation.",
                      created=recruiter)
        d_zoho = drive("Zoho", "Member Technical Staff", 8.4, 7.0, "CSE,IT,ECE,EEE", "Written Test,Programming Round,Advanced Programming,Technical Interview,HR Interview",
                       "OPEN", 2, 8, "C,Java,Data Structures,Algorithms", "Chennai,Tenkasi",
                       "Zoho is hiring Member Technical Staff to build products used by millions.\n\nYou will design, develop and maintain features across Zoho's suite. Strong problem solving in C/C++/Java is essential.")
        d_fw = drive("Freshworks", "Software Engineer I", 12.0, 7.5, "CSE,IT", "Online Assessment,Technical Interview 1,Technical Interview 2,HR Interview",
                     "OPEN", 9, 16, "Python,JavaScript,React,REST API,SQL", "Chennai,Bengaluru",
                     "Build delightful SaaS experiences. Work across Ruby/Python services and React frontends. Exposure to REST APIs and cloud is a plus.")
        d_acc = drive("Accenture", "Associate Software Engineer", 4.6, 6.0, "CSE,IT,ECE,EEE,MECH,AIDS", "Cognitive Assessment,Technical Assessment,Communication Assessment,Interview",
                      "OPEN", 12, 20, "Programming,SQL,Communication", "Pan India",
                      "Accenture ASE role. Work on technology delivery across clients after training.")
        d_amz = drive("Amazon", "SDE-1", 28.0, 8.0, "CSE,IT,AIDS", "Online Assessment,Technical Interview 1,Technical Interview 2,Bar Raiser",
                      "OPEN", 15, 25, "Data Structures,Algorithms,Java,System Design", "Bengaluru,Hyderabad",
                      "Design, build and operate highly scalable distributed systems. Strong CS fundamentals required.")
        d_tcs = drive("TCS", "Ninja / Digital", 3.6, 6.0, "CSE,IT,ECE,EEE,MECH,AIDS", "TCS NQT,Technical Interview,Managerial Interview,HR Interview",
                      "COMPLETED", -75, -60, "Programming,SQL", "Pan India", "TCS National Qualifier Test based hiring. Digital profile for top performers (7 LPA).")
        d_inf = drive("Infosys", "Systems Engineer", 3.6, 6.0, "CSE,IT,ECE,EEE", "Online Test,Technical Interview,HR Interview",
                      "COMPLETED", -50, -40, "Java,Python,SQL", "Mysuru,Pune,Chennai", "Infosys Systems Engineer hiring through InfyTQ certification route.")
        d_wip = drive("Wipro", "Project Engineer", 3.5, 6.0, "CSE,IT,ECE,EEE,MECH", std_rounds, "COMPLETED", -40, -30, "Programming,Communication",
                      "Pan India", "Wipro Elite NTH hiring.")
        d_hcl = drive("HCLTech", "Graduate Engineer Trainee", 4.25, 6.5, "CSE,IT,ECE", std_rounds, "COMPLETED", -30, -21, "Java,SQL,Networking",
                      "Noida,Chennai", "GET programme with training in latest technologies.")
        d_lt = drive("L&T Technology Services", "Graduate Engineer Trainee – Embedded", 5.0, 6.5, "ECE,EEE,MECH", "Aptitude Test,Technical Interview,HR Interview",
                     "CLOSED", -3, 3, "C,Embedded Systems,Electronics", "Vadodara,Chennai,Bengaluru", "Embedded systems and engineering R&D.")
        drive("Cognizant", "GenC Next – Full Stack Developer", 6.75, 7.0, "CSE,IT", "Online Assessment,Technical Interview,HR Interview", "PENDING_APPROVAL",
              14, 21, "Java,React,SQL,Spring Boot", "Chennai,Bengaluru", "Higher-package GenC Next role for strong full-stack candidates.", created=recruiter)

        def eligible(st, cg, br):
            return st["cgpa"] >= cg and st["dept"] in br.split(",") and st["ab"] == 0 and st["batch"] == BATCH

        offers_by_student = {}

        def add_app(did, sid, status, when, round_name=None):
            aid = s.ins("sp_applications", {"drive_id": did, "student_id": sid, "status": status,
                                            "current_round": round_name or status.replace("_", " ").title(),
                                            "applied_at": when, "updated_at": when + timedelta(days=3)})
            path = ["APPLIED", "SHORTLISTED", "ASSESSMENT", "TECH_INTERVIEW", "HR_INTERVIEW", "SELECTED"]
            steps = path[:path.index(status) + 1] if status in path else ["APPLIED", "SHORTLISTED", status]
            for k, stt in enumerate(steps):
                s.ex("INSERT INTO sp_app_history(application_id,status,note,changed_by,changed_at) VALUES(:a,:s,:n,:u,:t)",
                     {"a": aid, "s": stt, "n": "Registered; eligibility check passed" if stt == "APPLIED" else None, "u": tpo,
                      "t": when + timedelta(days=k)})
            return aid

        # completed drives with outcomes and offers
        for did, comp, role, ctc, cg, br, rate, loc in [(d_tcs, "TCS", "Ninja / Digital", 3.6, 6.0, "CSE,IT,ECE,EEE,MECH,AIDS", .30, "Chennai"),
                                                        (d_inf, "Infosys", "Systems Engineer", 3.6, 6.0, "CSE,IT,ECE,EEE", .22, "Mysuru"),
                                                        (d_wip, "Wipro", "Project Engineer", 3.5, 6.0, "CSE,IT,ECE,EEE,MECH", .2, "Bengaluru"),
                                                        (d_hcl, "HCLTech", "Graduate Engineer Trainee", 4.25, 6.5, "CSE,IT,ECE", .18, "Chennai")]:
            when = NOW - timedelta(days={d_tcs: 80, d_inf: 55, d_wip: 45, d_hcl: 35}[did])
            for st in students:
                if st["id"] == demo or not eligible(st, cg, br) or random.random() > .75:
                    continue
                r = random.random()
                status = "SELECTED" if r < rate else random.choice(["REJECTED", "REJECTED", "REJECTED"])
                aid = add_app(did, st["id"], status, when)
                if status == "SELECTED":
                    real_ctc = 7.0 if comp == "TCS" and st["cgpa"] > 8.3 else ctc
                    multi = offers_by_student.get(st["id"], 0)
                    s.ins("sp_offers", {"student_id": st["id"], "application_id": aid, "company_name": comp,
                                        "job_role": "Digital" if real_ctc == 7.0 else role, "ctc_lpa": real_ctc, "location": loc,
                                        "joining_date": NOW + timedelta(days=random.randint(150, 240)), "source": "CAMPUS",
                                        "status": "ACCEPTED" if multi == 0 else random.choice(["ACCEPTED", "DECLINED"]), "created_by": tpo})
                    offers_by_student[st["id"]] = multi + 1
                    s.ex("UPDATE sp_students SET placement_status='PLACED' WHERE user_id=:u", {"u": st["id"]})
        # a few higher-package off-campus / early offers for analytics spread
        for st in [x for x in students if x["cgpa"] > 8.6 and x["id"] != demo][:5]:
            comp, role, ctc = random.choice([("Microsoft", "Software Engineer", 44.0), ("Zoho", "MTS", 8.4), ("PayPal", "SDE", 18.5),
                                             ("Freshworks", "Software Engineer", 12.0), ("Deloitte", "Analyst", 7.6)])
            s.ins("sp_offers", {"student_id": st["id"], "company_name": comp, "job_role": role, "ctc_lpa": ctc, "location": "Bengaluru",
                                "source": "OFF_CAMPUS", "status": "ACCEPTED", "created_by": st["id"]})
            s.ex("UPDATE sp_students SET placement_status='PLACED' WHERE user_id=:u", {"u": st["id"]})

        # demo student journey
        add_app(d_tcs, demo, "REJECTED", NOW - timedelta(days=80))
        a_inf = add_app(d_inf, demo, "SELECTED", NOW - timedelta(days=55))
        s.ins("sp_offers", {"student_id": demo, "application_id": a_inf, "company_name": "Infosys", "job_role": "Systems Engineer",
                            "ctc_lpa": 3.6, "location": "Mysuru", "joining_date": NOW + timedelta(days=200),
                            "accept_by": NOW + timedelta(days=6), "source": "CAMPUS", "status": "PENDING", "created_by": tpo})
        add_app(d_cog, demo, "SHORTLISTED", NOW - timedelta(days=2))
        add_app(d_hcl, demo, "HR_INTERVIEW", NOW - timedelta(days=35))
        # open-drive applicants
        for did, cg, br in [(d_cog, 6.5, "CSE,IT,ECE"), (d_zoho, 7.0, "CSE,IT,ECE,EEE"), (d_fw, 7.5, "CSE,IT"), (d_lt, 6.5, "ECE,EEE,MECH")]:
            for st in students:
                if st["id"] != demo and eligible(st, cg, br) and random.random() < .45:
                    add_app(did, st["id"], random.choice(["APPLIED", "APPLIED", "SHORTLISTED", "ASSESSMENT"]),
                            NOW - timedelta(days=random.randint(1, 4)), None)

        # ---------------- calendar events
        def ev(title, typ, days, hours=10, drive_id=None, venue=None, desc=None, dur=2):
            st = (NOW + timedelta(days=days)).replace(hour=hours, minute=0)
            return s.ins("sp_events", {"title": title, "event_type": typ, "start_at": st, "end_at": st + timedelta(hours=dur),
                                       "drive_id": drive_id, "venue": venue, "description": desc, "created_by": tpo})
        for did, comp, dl, dd in [(d_cog, "Cognizant", 5, 10), (d_zoho, "Zoho", 2, 8), (d_fw, "Freshworks", 9, 16), (d_acc, "Accenture", 12, 20),
                                  (d_amz, "Amazon", 15, 25), (d_lt, "L&T Technology Services", -3, 3)]:
            s.ins("sp_events", {"title": f"{comp} registration closes", "event_type": "DEADLINE", "start_at": NOW + timedelta(days=dl), "drive_id": did, "created_by": tpo})
            s.ins("sp_events", {"title": f"{comp} placement drive", "event_type": "DRIVE", "start_at": NOW + timedelta(days=dd, hours=2), "drive_id": did,
                                "venue": "Main Auditorium, Block A", "created_by": tpo})
        ev("Cognizant pre-placement talk", "PPT", 3, 11, d_cog, "Seminar Hall 2", "Company overview, GenC roles, Q&A with HR.")
        ev("Cognizant aptitude test", "APTITUDE", 10, 9, d_cog, "Computer Lab 1–4", "Quant, logical & verbal – 50 min.")
        ev("Cognizant coding assessment", "CODING", 10, 14, d_cog, "Computer Lab 1–4", "2 coding problems – 45 min.")
        ev("Cognizant technical interviews", "TECH", 11, 10, d_cog, "Placement Block, Rooms 101–108")
        ev("Cognizant HR interviews", "HR", 11, 15, d_cog, "Placement Block")
        ev("Cognizant results", "RESULT", 13, 17, d_cog)
        ev("Zoho written test", "APTITUDE", 8, 9, d_zoho, "Computer Lab 3")
        ev("L&T Technology Services technical interviews", "TECH", 3, 10, d_lt, "Placement Block")
        ev("Infosys offer acceptance deadline", "OFFER_DEADLINE", 6, 18, d_inf)
        for k, (title, typ, day, venue) in enumerate([("Aptitude training – Percentages & Ratios", "TRAINING", -14, "Seminar Hall 1"),
                                                      ("Aptitude training – Time & Work", "TRAINING", -7, "Seminar Hall 1"),
                                                      ("Soft skills: GD practice", "TRAINING", 0, "Seminar Hall 1"),
                                                      ("Mock interviews with alumni", "MOCK", 4, "Placement Block"),
                                                      ("Resume building workshop", "TRAINING", 6, "Seminar Hall 2"),
                                                      ("DSA bootcamp – Trees & Graphs", "TRAINING", 1, "Computer Lab 2")]):
            eid = ev(title, typ, day, 14 if day == 0 else 10, None, venue)
            if day < 0:
                for st in students:
                    if random.random() < .7:
                        s.ex("INSERT INTO sp_event_attendance(event_id,user_id) VALUES(:e,:u)", {"e": eid, "u": st["id"]})

        # ---------------- off-campus
        for comp, role, cat, jt, exp, ctc, loc, mode, dl, skills, src, ver in [
            ("Google", "Software Engineering Intern", "Software Development", "INTERNSHIP", "Fresher", "₹1.2 L/month", "Bengaluru, Hyderabad", "ONSITE", 20, "Data Structures,Algorithms,C++,Java", "Google Careers", 1),
            ("Microsoft", "Software Engineer", "Software Development", "FRESHER", "0-1", "₹44 LPA", "Hyderabad", "HYBRID", 14, "C#,Java,Data Structures,System Design", "Microsoft Careers", 1),
            ("Atlassian", "Graduate Software Engineer", "Software Development", "FULL_TIME", "Fresher", "₹35 LPA", "Bengaluru", "REMOTE", 25, "Java,Python,React", "Company website", 1),
            ("Mu Sigma", "Trainee Decision Scientist", "Data Science", "FRESHER", "Fresher", "₹5.5 LPA", "Bengaluru", "ONSITE", 10, "SQL,Excel,Statistics,Python", "Alumni referral", 1),
            ("Fractal Analytics", "Data Analyst", "Data Science", "FULL_TIME", "0-2", "₹8 LPA", "Mumbai, Bengaluru", "HYBRID", 18, "SQL,Power BI,Python", "LinkedIn", 1),
            ("Sarvam AI", "AI Research Intern", "AI/ML", "INTERNSHIP", "Fresher", "₹60k/month", "Bengaluru", "ONSITE", 12, "Python,PyTorch,NLP,Deep Learning", "Company website", 1),
            ("Tiger Analytics", "ML Engineer", "AI/ML", "FRESHER", "0-1", "₹9 LPA", "Chennai", "HYBRID", 22, "Python,Machine Learning,SQL,Docker", "Naukri", 0),
            ("Palo Alto Networks", "Security Analyst Intern", "Cybersecurity", "INTERNSHIP", "Fresher", "₹50k/month", "Bengaluru", "ONSITE", 9, "Networking,Linux,Cybersecurity", "Company website", 1),
            ("Quick Heal", "Associate Security Engineer", "Cybersecurity", "FRESHER", "0-1", "₹6 LPA", "Pune", "ONSITE", 16, "Networking,C,Malware Analysis", "Company website", 1),
            ("AWS", "Cloud Support Associate", "Cloud", "FRESHER", "Fresher", "₹12 LPA", "Hyderabad, Chennai", "ONSITE", 11, "Linux,Networking,AWS", "Amazon Jobs", 1),
            ("Thoughtworks", "Graduate Developer – DevOps", "DevOps", "FULL_TIME", "Fresher", "₹9 LPA", "Pune, Chennai", "HYBRID", 30, "Docker,Kubernetes,CI/CD,Python", "Company website", 1),
            ("Zoho", "QA Engineer", "QA", "FRESHER", "Fresher", "₹6 LPA", "Chennai", "ONSITE", 8, "Testing,Selenium,Java", "Alumni referral", 1),
            ("BrowserStack", "SDET Intern", "QA", "INTERNSHIP", "Fresher", "₹40k/month", "Mumbai", "REMOTE", 6, "Selenium,JavaScript,Testing", "LinkedIn", 0),
            ("Deloitte", "Business Analyst", "Business Analyst", "FRESHER", "0-1", "₹7.6 LPA", "Hyderabad, Bengaluru", "HYBRID", 19, "Excel,SQL,Communication,Power BI", "Deloitte Careers", 1),
            ("Razorpay", "Frontend Engineer", "Software Development", "FULL_TIME", "0-2", "₹18 LPA", "Bengaluru", "HYBRID", 21, "JavaScript,React,CSS", "Company website", 1),
            ("Startup XYZ", "Web Developer Intern", "Software Development", "INTERNSHIP", "Fresher", "Unpaid", "Remote", "REMOTE", -2, "HTML,CSS,JavaScript", "Telegram group", 0),
        ]:
            s.ins("sp_offcampus", {"company": comp, "job_role": role, "category": cat, "job_type": jt, "experience": exp, "ctc": ctc,
                                   "location": loc, "work_mode": mode, "deadline": NOW + timedelta(days=dl), "skills": skills, "source": src,
                                   "verified": ver, "status": "APPROVED", "batch": f"{BATCH - 1}, {BATCH}",
                                   "apply_url": f"https://careers.{comp.lower().replace(' ', '')}.com", "posted_by": tpo,
                                   "description": f"{comp} is hiring a {role}.\n\nWhat you'll do:\n- Work with experienced engineers on real products\n- Learn {skills.split(',')[0]} in production\n\nRequirements: {skills.replace(',', ', ')}. Graduating batch {BATCH - 1}/{BATCH}."})
        s.ins("sp_offcampus", {"company": "Fintech Startup", "job_role": "Backend Intern", "category": "Software Development", "job_type": "INTERNSHIP",
                               "experience": "Fresher", "ctc": "₹25k/month", "location": "Remote", "work_mode": "REMOTE",
                               "deadline": NOW + timedelta(days=10), "skills": "Node.js,MongoDB", "source": "Alumni referral",
                               "verified": 0, "status": "PENDING", "posted_by": alumni[0], "apply_url": "https://example.com/apply",
                               "description": "Referral from alumni network."})

        # ---------------- learning content
        quiz_ids = {}
        for title, cat, topic, comp, dur, desc, qs in C.QUIZZES:
            qid = s.ins("sp_quizzes", {"title": title, "category": cat, "topic": topic, "company": comp, "duration_min": dur,
                                       "description": desc, "created_by": faculty})
            quiz_ids[title] = (qid, qs)
            for qtype, text, opts, ans, expl, tp in qs:
                s.ex("INSERT INTO sp_questions(quiz_id,qtype,question_text,options_json,answer,explanation,topic,marks) "
                     "VALUES(:q,:t,:x,:o,:a,:e,:tp,1)", {"q": qid, "t": qtype, "x": text, "o": json.dumps(opts) if opts else None,
                                                         "a": ans, "e": expl, "tp": tp})
        step_ids = {}
        for k, (name, tag, desc, icon, color, steps) in enumerate(C.PATHS):
            pid = s.ins("sp_paths", {"name": name, "skill_tag": tag, "description": desc, "icon": icon, "color": color, "seq": k})
            step_ids[tag] = []
            for n, (title, kind, content, url, qt) in enumerate(steps, 1):
                step_ids[tag].append(s.ins("sp_path_steps", {"path_id": pid, "seq": n, "title": title, "content_kind": kind,
                                                             "content": content, "resource_url": url,
                                                             "quiz_id": quiz_ids[qt][0] if qt else None}))
        for title, diff, topic, stmt, inf, outf, samples, tests, sol, pts in C.PROBLEMS:
            s.ins("sp_problems", {"title": title, "difficulty": diff, "topic": topic, "statement": stmt, "input_format": inf,
                                  "output_format": outf, "samples_json": json.dumps(samples), "tests_json": json.dumps(tests),
                                  "solution": sol, "points": pts, "starter_code": "# Read from standard input and print the answer\n"})
        for cat, topic, comp, q, ans, tips, kw in C.IQ:
            s.ins("sp_interview_questions", {"category": cat, "topic": topic, "company": comp, "question_text": q,
                                             "model_answer": ans, "tips": tips or None, "keywords": kw or None})

        # progress, attempts and submissions (demo + simulated cohort)
        for tag, n in (("JAVA", 6), ("SQL", 4), ("DSA", 4), ("APTITUDE", 4), ("COMM", 3), ("CLOUD", 2)):
            for sid in step_ids[tag][:n]:
                s.ex("INSERT INTO sp_step_progress(user_id,step_id) VALUES(:u,:s)", {"u": demo, "s": sid})
        demo_scores = {"Java Fundamentals": 0.86, "SQL Queries": 0.71, "Data Structures": 0.57, "Quantitative Aptitude – Set 1": 0.71,
                       "Logical Reasoning – Set 1": 0.8, "Verbal Ability – Set 1": 0.8, "Mock Placement Test 1": 0.7, "Operating Systems": 0.6}
        for st in students:
            is_demo = st["id"] == demo
            titles = list(demo_scores) if is_demo else random.sample(list(quiz_ids), random.randint(0, 6))
            for t in titles:
                qid, qs = quiz_ids[t]
                base = demo_scores[t] if is_demo else min(1, max(0.1, random.gauss(0.45 + (st["cgpa"] - 6) * 0.1, 0.18)))
                answers, topics, sc = {}, {}, 0
                for i, q in enumerate(qs):
                    ok = (i / len(qs)) < base if is_demo else random.random() < base
                    sc += ok
                    answers[str(i)] = {"given": [], "ok": ok}
                    tt = topics.setdefault(q[5], [0, 0])
                    tt[0] += ok
                    tt[1] += 1
                s.ex("INSERT INTO sp_attempts(quiz_id,user_id,score,max_score,accuracy,time_sec,answers_json,topic_json,submitted_at) "
                     "VALUES(:q,:u,:s,:m,:a,:t,:aj,:tj,:w)",
                     {"q": qid, "u": st["id"], "s": sc, "m": len(qs), "a": round(100 * sc / len(qs), 1), "t": random.randint(200, 700),
                      "aj": "{}", "tj": json.dumps(topics), "w": NOW - timedelta(days=random.randint(1, 30))})
        s.cur.execute("SELECT id FROM sp_problems ORDER BY id")
        pids = [r[0] for r in s.cur.fetchall()]
        for st in students:
            k = 4 if st["id"] == demo else random.randint(0, 5)
            for pid in pids[:k]:
                s.ex("INSERT INTO sp_submissions(problem_id,user_id,lang,code,passed,total,verdict) VALUES(:p,:u,'python','# solved',4,4,'ACCEPTED')",
                     {"p": pid, "u": st["id"]})
        sess = s.ins("sp_interview_sessions", {"user_id": demo, "category": "HR", "avg_score": 6.5, "completed": 1})
        s.cur.execute("SELECT id FROM sp_interview_questions WHERE category='HR' ORDER BY id FETCH FIRST 2 ROWS ONLY")
        for (qid,), (resp, scv) in zip(s.cur.fetchall(), [("I am Arun, a final-year CSE student. I built a smart attendance system using Spring Boot and completed a cloud internship at Accenture where I automated EC2 provisioning. I am looking for a backend role where I can grow.", 7.0),
                                                          ("You should hire me because I learn quickly and I have done projects.", 6.0)]):
            s.ex("INSERT INTO sp_interview_answers(session_id,question_id,response,score,feedback) VALUES(:s,:q,:r,:sc,:f)",
                 {"s": sess, "q": qid, "r": resp, "sc": scv, "f": "Good length for a spoken answer.\nAdd specifics – numbers, technologies or outcomes make answers memorable."})

        # ---------------- community
        def fq(uid, title, body, tags, ans):
            qid = s.ins("sp_forum_questions", {"user_id": uid, "title": title, "body": body, "tags": tags, "views": random.randint(20, 300)})
            first_a = None
            for au, text, ver in ans:
                aid = s.ins("sp_forum_answers", {"question_id": qid, "user_id": au, "body": text, "is_verified": ver})
                first_a = first_a or aid
                s.ex("INSERT INTO sp_votes(user_id,target_type,target_id,vote_val) VALUES(:u,'A',:a,1)", {"u": students[3]["id"], "a": aid})
            return qid, first_a
        q1, a1 = fq(students[5]["id"], "What is the difference between INNER JOIN and LEFT JOIN in SQL?",
                    "I keep getting confused in interviews. Can someone explain with an example?", "SQL,DBMS,Interview",
                    [(faculty, "**INNER JOIN** returns only rows that match in both tables.\n\n**LEFT JOIN** returns *all* rows from the left table and the matching rows from the right; when there is no match the right-side columns are `NULL`.\n\n```sql\nSELECT e.name, d.dept_name\nFROM emp e LEFT JOIN dept d ON d.id = e.dept_id;\n```\nEmployees without a department still appear, with `dept_name` = NULL.", 1),
                     (demo, "Think of INNER as the intersection and LEFT as 'everything on the left + matches'.", 0)])
        s.ex("UPDATE sp_forum_questions SET accepted_answer_id=:a WHERE id=:q", {"a": a1, "q": q1})
        fq(demo, "How to prepare for Zoho's advanced programming round?", "Zoho drive is coming up. What kind of problems are asked in round 3?",
           "Placement,DSA,Zoho", [(alumni[0], "Expect 2–3 hours of building a small application (e.g., a railway reservation system) in C/Java with menus and data structures. Practise designing classes and handling edge cases. Clean code matters.", 0)])
        fq(students[8]["id"], "Is it OK to use Python in coding rounds of service companies?", "Most companies list Java/C. Will Python be accepted?",
           "Python,Placement", [(tpo, "Yes – Cognizant, Accenture, TCS and Infosys platforms allow Python. Check the drive notice for any restriction.", 1)])
        fq(students[12]["id"], "Best resources for learning Operating Systems for interviews?", "", "OS,Interview", [])
        fq(students[20]["id"], "Should I take a startup offer or wait for an MNC?", "I have an offer from a startup (6 LPA). Seniors, what would you suggest?",
           "Alumni,Placement", [(alumni[2], "Consider learning opportunity, mentorship, stability and the tech stack. Talk to employees. A good startup can accelerate your growth.", 0)])
        s.ex("UPDATE sp_forum_questions SET audience='ALUMNI' WHERE title LIKE 'Should I take a startup%'")
        s.ex("INSERT INTO sp_tag_follows(user_id,tag) VALUES(:u,'SQL')", {"u": demo})
        for t, b, cat, link in [
            ("Cognizant GenC drive – PPT on " + (NOW + timedelta(days=3)).strftime("%d %b"), "All eligible CSE/IT/ECE students must attend the pre-placement talk. Formal dress code.", "GENERAL", None),
            ("Smart India Hackathon 2026 – internal round", "Register your team of 6 by the end of this month. Problem statements are on the SIH portal.", "HACKATHON", "https://sih.gov.in"),
            ("Post-matric scholarship applications open", "Eligible students can apply on the National Scholarship Portal before the deadline.", "SCHOLARSHIP", "https://scholarships.gov.in/"),
            ("GATE 2027 – free mock test series", "The department is conducting weekly GATE mock tests every Saturday at 10 AM in Lab 3.", "HIGHER_STUDIES", None),
            ("Aptitude formula sheet uploaded", "Download the consolidated quantitative aptitude formula sheet from the Skill Builder → Aptitude Mastery path.", "PREP_MATERIAL", None),
            ("Workshop: Cloud computing with AWS", "Two-day hands-on workshop by AWS Academy. Limited to 60 seats.", "EVENT", None),
        ]:
            s.ins("sp_announcements", {"title": t, "body": b, "category": cat, "link": link, "created_by": tpo})
        for uid, comp, role, yr, rounds, content, rating, tips, outcome in [
            (alumni[2], "Accenture", "Associate Software Engineer", BATCH - 4, "Cognitive → Technical → Communication → Interview",
             "The cognitive test had English, critical reasoning and abstract reasoning. Technical had pseudo-code and MS Office questions. The interview was friendly – asked about my project and OOP basics.", 4, "Practise pseudo-code questions and speak clearly in the communication round.", "SELECTED"),
            (alumni[0], "Amazon", "SDE-1", BATCH - 5, "Online Assessment → Technical 1 → Technical 2 → Bar Raiser",
             "OA had 2 coding questions (graphs + DP) and a work-style survey. Interviews focused on DSA and leadership principles with STAR stories.", 5, "Do 200+ LeetCode problems and prepare 8 STAR stories mapped to Amazon LPs.", "SELECTED"),
            (students[10]["id"], "TCS", "Ninja", BATCH, "TCS NQT → Technical → Managerial → HR",
             "NQT had numerical, verbal, reasoning and 2 coding questions. The technical interview asked about my project, SQL joins and Java OOP.", 4, "Revise basics thoroughly and know your project inside out.", "SELECTED"),
            (students[14]["id"], "Zoho", "MTS", BATCH - 1, "Written → Programming → Advanced Programming → Technical → HR",
             "Round 3 was building a mini application for 3 hours. They check logic and code structure more than UI.", 4, "Practise C programming without IDE help.", "REJECTED"),
        ]:
            s.ins("sp_experiences", {"user_id": uid, "company": comp, "job_role": role, "exp_year": yr, "rounds_desc": rounds,
                                     "content": content, "rating": rating, "tips": tips, "outcome": outcome})
        s.ins("sp_mentorship", {"student_id": demo, "mentor_id": alumni[0], "topic": "Preparing for Amazon SDE-1",
                                "message": "Hi sir, I'm targeting the Amazon drive. How should I plan the next 3 weeks?",
                                "reply": "Focus on arrays, trees, graphs and DP – 4 problems a day. Prepare STAR stories for leadership principles. Do 2 full mock interviews.",
                                "status": "ANSWERED", "replied_at": NOW - timedelta(days=1)})
        s.ins("sp_mentorship", {"student_id": students[7]["id"], "mentor_id": alumni[1], "topic": "Data science career path",
                                "message": "Should I learn deep learning before applying for data analyst roles?", "status": "OPEN"})
        s.ins("sp_tickets", {"user_id": students[9]["id"], "subject": "CGPA not updated after revaluation", "body": "My CGPA changed to 7.4 after revaluation. Please update.",
                             "category": "Profile / Eligibility", "status": "OPEN"})
        s.ins("sp_tickets", {"user_id": demo, "subject": "Unable to upload offer letter", "body": "Getting an error while uploading.", "category": "Technical issue",
                             "status": "RESOLVED", "response": "Fixed – please try again. Max size is 8 MB."})
        for st in students[:40]:
            s.ex("INSERT INTO sp_feedback(user_id,category,rating,comments) VALUES(:u,:c,:r,:m)",
                 {"u": st["id"], "c": random.choice(["Placement Training", "Mock Interviews", "Portal Experience", "Learning Content"]),
                  "r": random.choice([3, 4, 4, 5, 5]), "m": random.choice([None, "Very useful sessions", "Need more coding practice", "Great portal!"])})
        for t, m, link, cat in [("Shortlisted: Cognizant", "You have been shortlisted for the Cognizant aptitude test.", "/applications", "APPLICATION"),
                                ("Offer letter from Infosys", "You have received an offer for Systems Engineer. Respond on the Offers page.", "/offers", "OFFER"),
                                ("New drive: Zoho", "Zoho is hiring Member Technical Staff (₹8.4 LPA). You are eligible – register now.", f"/drives/{d_zoho}", "DRIVE"),
                                ("Your mentor replied", "Vignesh Subramanian replied to: Preparing for Amazon SDE-1", "/mentoring", "MENTORING")]:
            s.ex("INSERT INTO sp_notifications(user_id,title,message,link,category) VALUES(:u,:t,:m,:l,:c)", {"u": demo, "t": t, "m": m, "l": link, "c": cat})
        self.con.commit()
        print(f"  seeded {len(students)} students, {len(companies) + 1} companies, demo data and learning content")


def main():
    reset = "--reset" in sys.argv
    with conn() as con:
        cur = con.cursor()
        print(f"Connected to Oracle {con.version} as {Config.DB_USER}@{Config.DB_SERVICE}")
        if reset:
            drop_all(cur)
        run_schema(cur)
        cur.execute("SELECT COUNT(*) FROM sp_users")
        if cur.fetchone()[0] == 0:
            Seeder(con).run()
        else:
            print("  data already present – skipping seed (use --reset to rebuild)")
    print("Done.")


if __name__ == "__main__":
    main()
