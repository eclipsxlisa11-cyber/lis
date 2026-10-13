"""TapIn core logic: tap handling, anti-passback, attendance zones, warnings.

Every tap goes through handle_tap(). The dashboard only reads from the database,
so the reader script and the web app can run as separate processes.
"""
import os
import smtplib
import sqlite3
from email.message import EmailMessage

DB_PATH = os.environ.get("TAPIN_DB", os.path.join(os.path.dirname(__file__), "tapin.db"))

# ---- Policy (all in one place so the report can cite and justify them) -------
DEBOUNCE_S = 10          # a second tap within 10 s is a double tap and is blocked
EARLY_OPEN_MIN = 15      # taps accepted this long before a session starts
CLOSE_GRACE_MIN = 30     # taps accepted this long after a session ends
LATE_AFTER_MIN = 10      # tap in after this many minutes counts as late
EARLY_LEAVE_FRAC = 0.75  # stayed less than 75% of the session => early leaver
GREEN_MIN = 80           # attendance % at or above => green zone
RED_BELOW = 60           # attendance % below => red zone (amber in between)
WARN_COOLDOWN_DAYS = 7   # do not warn the same student more than once a week
# Credit per session: a full tap in + tap out earns 1; an incomplete record earns half.
CREDIT = {"COMPLETE": 1.0, "PRESENT": 1.0, "MISSING_OUT": 0.5, "EARLY_LEAVE": 0.5, "ABSENT": 0.0}

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  uid TEXT PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, email TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, start_ts REAL NOT NULL, end_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS taps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, ts REAL NOT NULL,
  session_id INTEGER, event TEXT NOT NULL, note TEXT);
CREATE INDEX IF NOT EXISTS idx_taps_session ON taps(session_id, uid, ts);
CREATE TABLE IF NOT EXISTS warnings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, ts REAL NOT NULL,
  attendance_pct REAL, message TEXT, delivery TEXT);
"""


def connect(path=None):
    db = sqlite3.connect(path or DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# ---- Tap handling ------------------------------------------------------------
def active_session(db, now):
    return db.execute(
        "SELECT * FROM sessions WHERE ? BETWEEN start_ts - ?*60 AND end_ts + ?*60 "
        "ORDER BY start_ts LIMIT 1", (now, EARLY_OPEN_MIN, CLOSE_GRACE_MIN)).fetchone()


def _log(db, uid, now, sid, event, note=None):
    db.execute("INSERT INTO taps(uid, ts, session_id, event, note) VALUES (?,?,?,?,?)",
               (uid, now, sid, event, note))
    db.commit()


def handle_tap(db, uid, now):
    """Process one card read. Returns a dict the reader and dashboard can show.

    result is one of: IN, OUT, DUPLICATE, UNKNOWN, NO_SESSION.
    Only IN and OUT change attendance; the other three are logged as flags.
    """
    uid = str(uid)
    student = db.execute("SELECT * FROM students WHERE uid=?", (uid,)).fetchone()
    if not student:
        _log(db, uid, now, None, "UNKNOWN", "Card not registered")
        return {"result": "UNKNOWN", "flag": True, "message": "Card not registered"}

    sess = active_session(db, now)
    if not sess:
        _log(db, uid, now, None, "NO_SESSION", "Tap outside a class window")
        return {"result": "NO_SESSION", "flag": True, "student": student["name"],
                "message": "No class is open right now"}

    last_any = db.execute(
        "SELECT ts FROM taps WHERE uid=? AND session_id=? ORDER BY ts DESC LIMIT 1",
        (uid, sess["id"])).fetchone()
    if last_any and now - last_any["ts"] < DEBOUNCE_S:
        _log(db, uid, now, sess["id"], "DUPLICATE", f"{now - last_any['ts']:.0f}s after previous tap")
        return {"result": "DUPLICATE", "flag": True, "student": student["name"],
                "student_id": student["student_id"],
                "message": "Double tap blocked. Your first tap was already recorded"}

    last_valid = db.execute(
        "SELECT event FROM taps WHERE uid=? AND session_id=? AND event IN ('IN','OUT') "
        "ORDER BY ts DESC LIMIT 1", (uid, sess["id"])).fetchone()
    event = "OUT" if last_valid and last_valid["event"] == "IN" else "IN"
    _log(db, uid, now, sess["id"], event)

    out = {"result": event, "flag": False, "student": student["name"],
           "student_id": student["student_id"], "session": sess["name"],
           "message": ("Tapped in" if event == "IN" else "Tapped out") + f", {student['name']}"}
    if event == "IN" and now > sess["start_ts"] + LATE_AFTER_MIN * 60:
        out["message"] += " (late)"
    pct = student_summaries(db, now).get(student["student_id"], {}).get("pct")
    if pct is not None and pct < RED_BELOW:
        out["warning"] = f"Attendance at {pct:.0f}%. You are in the red zone"
    return out


# ---- Attendance derivation ---------------------------------------------------
def is_closed(sess, now):
    return now > sess["end_ts"] + CLOSE_GRACE_MIN * 60


def session_rows(db, sess, now):
    """One row per student for one session: tap in/out, dwell, status, late flag."""
    taps = db.execute(
        "SELECT uid, ts, event FROM taps WHERE session_id=? ORDER BY ts", (sess["id"],)).fetchall()
    by = {}
    for t in taps:
        by.setdefault(t["uid"], []).append(t)
    closed, dur = is_closed(sess, now), sess["end_ts"] - sess["start_ts"]
    rows = []
    for s in db.execute("SELECT * FROM students ORDER BY name"):
        ts = by.get(s["uid"], [])
        valid = [t for t in ts if t["event"] in ("IN", "OUT")]
        ins = [t for t in valid if t["event"] == "IN"]
        in_ts = ins[0]["ts"] if ins else None
        out_ts, dwell = None, None
        if not ins:
            status = "ABSENT" if closed else "NOT_YET"
        elif valid[-1]["event"] == "IN":
            status = "MISSING_OUT" if closed else "PRESENT"
        else:
            out_ts = valid[-1]["ts"]
            dwell = out_ts - in_ts
            status = "EARLY_LEAVE" if dwell < EARLY_LEAVE_FRAC * dur else "COMPLETE"
        rows.append({
            "student_id": s["student_id"], "name": s["name"], "uid": s["uid"],
            "in_ts": in_ts, "out_ts": out_ts, "dwell": dwell, "status": status,
            "late": bool(in_ts and in_ts > sess["start_ts"] + LATE_AFTER_MIN * 60),
            "doubles": sum(1 for t in ts if t["event"] == "DUPLICATE"),
        })
    return rows


def zone_of(pct):
    if pct is None:
        return "NONE"
    return "GREEN" if pct >= GREEN_MIN else "RED" if pct < RED_BELOW else "AMBER"


def student_summaries(db, now):
    """Attendance % per student across all closed sessions, plus issue counts."""
    sessions = [s for s in db.execute("SELECT * FROM sessions ORDER BY start_ts") if is_closed(s, now)]
    out = {s["student_id"]: {"credit": 0.0, "attended": 0, "missing_out": 0, "early_leave": 0,
                             "late": 0, "doubles": 0, "held": len(sessions)}
           for s in db.execute("SELECT student_id FROM students")}
    for sess in sessions:
        for r in session_rows(db, sess, now):
            o = out[r["student_id"]]
            o["credit"] += CREDIT[r["status"]]
            o["attended"] += r["status"] != "ABSENT"
            o["missing_out"] += r["status"] == "MISSING_OUT"
            o["early_leave"] += r["status"] == "EARLY_LEAVE"
            o["late"] += r["late"]
            o["doubles"] += r["doubles"]
    for o in out.values():
        o["pct"] = round(100 * o["credit"] / o["held"], 1) if o["held"] else None
        o["zone"] = zone_of(o["pct"])
    return out


def last_warning_ts(db, student_id):
    r = db.execute("SELECT MAX(ts) m FROM warnings WHERE student_id=?", (student_id,)).fetchone()
    return r["m"]


# ---- Warnings ----------------------------------------------------------------
def send_warning(db, student, pct, now):
    """Record a warning. Emails it if SMTP_HOST is set, otherwise logs it only."""
    msg = (f"Hi {student['name']}, your attendance for 31275 Mobile Networking is {pct:.0f}%, "
           f"below the {RED_BELOW}% threshold. Please tap in and out at every class "
           f"and contact your tutor if something is affecting your attendance.")
    delivery = "logged (SMTP not configured)"
    host = os.environ.get("SMTP_HOST")
    if host and student["email"]:
        try:
            m = EmailMessage()
            m["From"] = os.environ.get("SMTP_FROM", "tapin@localhost")
            m["To"], m["Subject"] = student["email"], "Attendance warning"
            m.set_content(msg)
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 25)), timeout=10) as s:
                s.send_message(m)
            delivery = "emailed"
        except Exception as e:  # keep the dashboard working if mail is down
            delivery = f"email failed: {e.__class__.__name__}"
    db.execute("INSERT INTO warnings(student_id, ts, attendance_pct, message, delivery) VALUES (?,?,?,?,?)",
               (student["student_id"], now, pct, msg, delivery))
    db.commit()
    return delivery


# ---- Dashboard payload -------------------------------------------------------
def dashboard(db, now, session_id=None):
    sessions = db.execute("SELECT * FROM sessions ORDER BY start_ts").fetchall()
    if not sessions:
        return {"empty": True}
    sel = next((s for s in sessions if s["id"] == session_id), None) \
        or active_session(db, now) \
        or max((s for s in sessions if s["start_ts"] <= now), key=lambda s: s["start_ts"], default=sessions[-1])
    rows = session_rows(db, sel, now)
    summ = student_summaries(db, now)
    names = {s["uid"]: (s["name"], s["student_id"]) for s in db.execute("SELECT * FROM students")}

    exceptions = []
    for r in rows:
        if r["status"] == "MISSING_OUT":
            exceptions.append({"type": "MISSING_OUT", "student": r["name"], "student_id": r["student_id"],
                               "ts": r["in_ts"], "detail": "Tapped in but never tapped out"})
        elif r["status"] == "EARLY_LEAVE":
            exceptions.append({"type": "EARLY_LEAVE", "student": r["name"], "student_id": r["student_id"],
                               "ts": r["out_ts"], "detail": f"Left after {round(r['dwell'] / 60)} min"})
    for t in db.execute("SELECT * FROM taps WHERE event='DUPLICATE' AND session_id=?", (sel["id"],)):
        n, sid = names.get(t["uid"], ("Unknown", "-"))
        exceptions.append({"type": "DUPLICATE", "student": n, "student_id": sid,
                           "ts": t["ts"], "detail": f"Double tap, {t['note']}. Blocked"})
    for t in db.execute("SELECT * FROM taps WHERE event IN ('UNKNOWN','NO_SESSION') ORDER BY ts DESC LIMIT 10"):
        n, sid = names.get(t["uid"], (f"Card {t['uid']}", "-"))
        exceptions.append({"type": t["event"], "student": n, "student_id": sid,
                           "ts": t["ts"], "detail": t["note"]})
    exceptions.sort(key=lambda e: e["ts"] or 0, reverse=True)

    zones = []
    for s in db.execute("SELECT * FROM students"):
        z = dict(summ[s["student_id"]], student_id=s["student_id"], name=s["name"], email=s["email"])
        lw = last_warning_ts(db, s["student_id"])
        z["last_warned"] = lw
        z["can_warn"] = z["zone"] == "RED" and (lw is None or now - lw > WARN_COOLDOWN_DAYS * 86400)
        zones.append(z)
    zones.sort(key=lambda z: (z["pct"] is None, z["pct"] if z["pct"] is not None else 0))

    turnout = []
    for s in sessions:
        if is_closed(s, now):
            rr = session_rows(db, s, now)
            turnout.append({"name": s["name"], "pct": round(100 * sum(r["status"] != "ABSENT" for r in rr) / len(rr))})

    feed = []
    for t in db.execute("SELECT * FROM taps ORDER BY ts DESC LIMIT 14"):
        n, sid = names.get(t["uid"], (f"Card {t['uid']}", "-"))
        feed.append({"ts": t["ts"], "student": n, "student_id": sid, "event": t["event"]})

    cnt = lambda st: sum(r["status"] == st for r in rows)
    return {
        "now": now,
        "sessions": [{"id": s["id"], "name": s["name"], "start": s["start_ts"], "end": s["end_ts"],
                      "closed": is_closed(s, now)} for s in sessions],
        "selected": {"id": sel["id"], "name": sel["name"], "start": sel["start_ts"], "end": sel["end_ts"],
                     "closed": is_closed(sel, now), "live": sel["start_ts"] - EARLY_OPEN_MIN * 60 <= now <= sel["end_ts"] + CLOSE_GRACE_MIN * 60},
        "kpi": {"enrolled": len(rows), "present_now": cnt("PRESENT"),
                "attended": sum(r["status"] != "ABSENT" and r["status"] != "NOT_YET" for r in rows),
                "not_yet": cnt("NOT_YET") + cnt("ABSENT"),
                "late": sum(r["late"] for r in rows),
                "flags": len(exceptions),
                "red": sum(z["zone"] == "RED" for z in zones),
                "amber": sum(z["zone"] == "AMBER" for z in zones),
                "green": sum(z["zone"] == "GREEN" for z in zones)},
        "rows": rows, "exceptions": exceptions, "zones": zones, "turnout": turnout, "feed": feed,
        "warnings": [dict(w) for w in db.execute(
            "SELECT w.*, s.name FROM warnings w JOIN students s USING(student_id) ORDER BY ts DESC LIMIT 8")],
        "policy": {"debounce_s": DEBOUNCE_S, "green_min": GREEN_MIN, "red_below": RED_BELOW,
                   "late_after_min": LATE_AFTER_MIN, "early_leave_pct": int(EARLY_LEAVE_FRAC * 100)},
    }
