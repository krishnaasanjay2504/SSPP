import csv
import io
from flask import Blueprint, render_template, request, Response, g
from .. import db
from ..security import roles_required

bp = Blueprint("analytics", __name__)

DIMENSIONS = {
    "department": ("Department", "s.department"),
    "batch": ("Batch", "TO_CHAR(s.batch_year)"),
    "gender": ("Gender", "s.gender"),
    "status": ("Placement status", "s.placement_status"),
}


def _filters():
    a = request.args
    w, p = [], {}
    if a.get("batch"):
        w.append("s.batch_year=:batch")
        p["batch"] = int(a["batch"])
    if a.get("dept"):
        w.append("s.department=:dept")
        p["dept"] = a["dept"]
    return (" AND " + " AND ".join(w)) if w else "", p


def kpis(where="", p=None):
    p = p or {}
    total = db.scalar(f"SELECT COUNT(*) FROM sp_students s WHERE 1=1 {where}", p)
    eligible = db.scalar(f"SELECT COUNT(*) FROM sp_students s WHERE NVL(s.active_backlogs,0)=0 AND NVL(s.cgpa,0)>=6 {where}", p)
    placed = db.scalar(f"SELECT COUNT(DISTINCT o.student_id) FROM sp_offers o JOIN sp_students s ON s.user_id=o.student_id "
                       f"WHERE o.status<>'DECLINED' {where}", p)
    offers = db.scalar(f"SELECT COUNT(*) FROM sp_offers o JOIN sp_students s ON s.user_id=o.student_id WHERE 1=1 {where}", p)
    multi = db.scalar(f"SELECT COUNT(*) FROM (SELECT o.student_id FROM sp_offers o JOIN sp_students s ON s.user_id=o.student_id "
                      f"WHERE 1=1 {where} GROUP BY o.student_id HAVING COUNT(*)>1)", p)
    ctc = db.one(f"SELECT MAX(o.ctc_lpa) hi, ROUND(AVG(o.ctc_lpa),2) avg, ROUND(MEDIAN(o.ctc_lpa),2) med FROM sp_offers o "
                 f"JOIN sp_students s ON s.user_id=o.student_id WHERE 1=1 {where}", p) or {}
    return {"total": total, "eligible": eligible, "placed": placed, "offers": offers, "multi": multi,
            "pct": round(100 * placed / eligible, 1) if eligible else 0, "highest": ctc.get("hi") or 0,
            "average": ctc.get("avg") or 0, "median": ctc.get("med") or 0,
            "companies": db.scalar("SELECT COUNT(*) FROM sp_companies WHERE status='APPROVED'"),
            "drives": db.scalar("SELECT COUNT(*) FROM sp_drives WHERE status IN ('OPEN','CLOSED','COMPLETED')")}


def dept_stats(where="", p=None):
    return db.query(f"""SELECT s.department dim, COUNT(*) total,
                        SUM(CASE WHEN EXISTS (SELECT 1 FROM sp_offers o WHERE o.student_id=s.user_id AND o.status<>'DECLINED') THEN 1 ELSE 0 END) placed,
                        (SELECT COUNT(*) FROM sp_offers o JOIN sp_students s2 ON s2.user_id=o.student_id WHERE s2.department=s.department) offers,
                        (SELECT MAX(o.ctc_lpa) FROM sp_offers o JOIN sp_students s2 ON s2.user_id=o.student_id WHERE s2.department=s.department) highest,
                        (SELECT ROUND(AVG(o.ctc_lpa),2) FROM sp_offers o JOIN sp_students s2 ON s2.user_id=o.student_id WHERE s2.department=s.department) average
                        FROM sp_students s WHERE s.department IS NOT NULL {where} GROUP BY s.department ORDER BY s.department""", p or {})


def company_stats(where="", p=None):
    return db.query(f"""SELECT o.company_name dim, COUNT(*) offers, MAX(o.ctc_lpa) ctc,
                        COUNT(DISTINCT o.job_role) roles FROM sp_offers o JOIN sp_students s ON s.user_id=o.student_id
                        WHERE 1=1 {where} GROUP BY o.company_name ORDER BY offers DESC""", p or {})


def ctc_bands(where="", p=None):
    return db.query(f"""SELECT band dim, COUNT(*) n FROM (SELECT CASE WHEN o.ctc_lpa < 4 THEN '< 4 LPA'
                        WHEN o.ctc_lpa < 6 THEN '4–6 LPA' WHEN o.ctc_lpa < 10 THEN '6–10 LPA' WHEN o.ctc_lpa < 20 THEN '10–20 LPA'
                        ELSE '20+ LPA' END band, CASE WHEN o.ctc_lpa < 4 THEN 1 WHEN o.ctc_lpa < 6 THEN 2 WHEN o.ctc_lpa < 10 THEN 3
                        WHEN o.ctc_lpa < 20 THEN 4 ELSE 5 END ord FROM sp_offers o JOIN sp_students s ON s.user_id=o.student_id
                        WHERE 1=1 {where}) GROUP BY band, ord ORDER BY ord""", p or {})


@bp.route("/tpo")
@roles_required("TPO", "ADMIN", "FACULTY")
def tpo_dashboard():
    k = kpis()
    depts = dept_stats()
    comps = company_stats()[:10]
    bands = ctc_bands()
    funnel = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM sp_applications GROUP BY status")}
    pending = {
        "users": db.scalar("SELECT COUNT(*) FROM sp_users WHERE is_approved=0 AND is_active=1"),
        "companies": db.scalar("SELECT COUNT(*) FROM sp_companies WHERE status='PENDING'"),
        "drives": db.scalar("SELECT COUNT(*) FROM sp_drives WHERE status='PENDING_APPROVAL'"),
        "jobs": db.scalar("SELECT COUNT(*) FROM sp_offcampus WHERE status='PENDING'"),
        "tickets": db.scalar("SELECT COUNT(*) FROM sp_tickets WHERE status<>'RESOLVED'"),
    }
    upcoming = db.query("SELECT e.*, c.name company FROM sp_events e LEFT JOIN sp_drives d ON d.id=e.drive_id LEFT JOIN "
                        "sp_companies c ON c.id=d.company_id WHERE e.start_at >= SYSTIMESTAMP ORDER BY e.start_at "
                        "FETCH FIRST 6 ROWS ONLY")
    open_drives = db.query("SELECT d.*, c.name company, (SELECT COUNT(*) FROM sp_applications a WHERE a.drive_id=d.id) applicants "
                           "FROM sp_drives d JOIN sp_companies c ON c.id=d.company_id WHERE d.status='OPEN' ORDER BY d.drive_date")
    return render_template("admin/dashboard.html", k=k, depts=depts, comps=comps, bands=bands, funnel=funnel,
                           pending=pending, upcoming=upcoming, open_drives=open_drives)


@bp.route("/analytics")
@roles_required("TPO", "ADMIN", "FACULTY")
def reports():
    where, p = _filters()
    k = kpis(where, p)
    dim = request.args.get("dim", "department")
    if dim == "company":
        rows = company_stats(where, p)
    elif dim == "ctc":
        rows = ctc_bands(where, p)
    elif dim == "role":
        rows = db.query(f"SELECT o.job_role dim, COUNT(*) offers, ROUND(AVG(o.ctc_lpa),2) ctc FROM sp_offers o JOIN sp_students s "
                        f"ON s.user_id=o.student_id WHERE 1=1 {where} GROUP BY o.job_role ORDER BY offers DESC", p)
    elif dim == "skill":
        rows = db.query(f"""SELECT INITCAP(LOWER(i.title)) dim, COUNT(DISTINCT i.user_id) students,
                            COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM sp_offers o WHERE o.student_id=i.user_id) THEN i.user_id END) placed
                            FROM sp_profile_items i JOIN sp_students s ON s.user_id=i.user_id WHERE i.item_type='SKILL' {where}
                            GROUP BY INITCAP(LOWER(i.title)) ORDER BY students DESC FETCH FIRST 20 ROWS ONLY""", p)
    else:
        label, col = DIMENSIONS.get(dim, DIMENSIONS["department"])
        rows = db.query(f"""SELECT NVL({col},'Not specified') dim, COUNT(*) total,
                            SUM(CASE WHEN EXISTS (SELECT 1 FROM sp_offers o WHERE o.student_id=s.user_id AND o.status<>'DECLINED') THEN 1 ELSE 0 END) placed,
                            ROUND(AVG(s.cgpa),2) avg_cgpa FROM sp_students s WHERE 1=1 {where} GROUP BY {col} ORDER BY 1""", p)
        for r in rows:
            r["pct"] = round(100 * r["placed"] / r["total"], 1) if r["total"] else 0
    if request.args.get("export"):
        out = io.StringIO()
        w = csv.writer(out)
        if rows:
            w.writerow([c.replace("_", " ").title() for c in rows[0].keys()])
            for r in rows:
                w.writerow(list(r.values()))
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=placement_report_{dim}.csv"})
    batches = [r["batch_year"] for r in db.query("SELECT DISTINCT batch_year FROM sp_students WHERE batch_year IS NOT NULL ORDER BY 1")]
    return render_template("admin/reports.html", k=k, rows=rows, dim=dim, batches=batches, a=request.args)
