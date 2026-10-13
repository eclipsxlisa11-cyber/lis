"""TapIn dashboard server. Run: python app.py   then open http://<pi-address>:5000"""
import csv
import io
import time

from flask import Flask, Response, jsonify, render_template, request

import logic
import seed as seeder

app = Flask(__name__)


def db():
    return logic.connect()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/dashboard")
def api_dashboard():
    sid = request.args.get("session", type=int)
    return jsonify(logic.dashboard(db(), time.time(), sid))


@app.route("/api/tap", methods=["POST"])
def api_tap():
    """Used by the dashboard's simulator. The real reader calls logic.handle_tap directly."""
    uid = (request.get_json(silent=True) or {}).get("uid", "")
    if not str(uid).strip():
        return jsonify(error="uid required"), 400
    return jsonify(logic.handle_tap(db(), str(uid).strip(), time.time()))


@app.route("/api/warn", methods=["POST"])
def api_warn():
    body = request.get_json(silent=True) or {}
    conn, now = db(), time.time()
    summ = logic.student_summaries(conn, now)
    ids = body.get("student_ids") or []
    sent = []
    for s in conn.execute("SELECT * FROM students"):
        info = summ[s["student_id"]]
        if s["student_id"] in ids and info["zone"] == "RED":
            last = logic.last_warning_ts(conn, s["student_id"])
            if last and now - last < logic.WARN_COOLDOWN_DAYS * 86400:
                continue
            sent.append({"student_id": s["student_id"],
                         "delivery": logic.send_warning(conn, s, info["pct"], now)})
    return jsonify(sent=sent)


@app.route("/api/reset-demo", methods=["POST"])
def api_reset():
    seeder.seed(db())
    return jsonify(ok=True)


@app.route("/api/export.csv")
def export_csv():
    conn, now = db(), time.time()
    d = logic.dashboard(conn, now, request.args.get("session", type=int))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["student_id", "name", "tap_in", "tap_out", "dwell_min", "status", "late", "double_taps"])
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""
    for r in d["rows"]:
        w.writerow([r["student_id"], r["name"], fmt(r["in_ts"]), fmt(r["out_ts"]),
                    round(r["dwell"] / 60) if r["dwell"] else "", r["status"], r["late"], r["doubles"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_{d['selected']['name'].replace(' ', '_')}.csv"})


if __name__ == "__main__":
    c = db()
    if not c.execute("SELECT 1 FROM students LIMIT 1").fetchone():
        seeder.seed(c)  # first run: demo data so the dashboard is not empty
    app.run(host="0.0.0.0", port=5000)
