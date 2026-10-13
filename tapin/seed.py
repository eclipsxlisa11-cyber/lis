"""Fill the database with demo data: 8 past classes plus one running right now.

Past taps go through handle_tap() with their original timestamps, so the demo data
is produced by the same rules the live system uses (doubles get blocked, etc.).
"""
import random
import time

import logic

FIRST = ["Aarav", "Mia", "Liam", "Sophie", "Noah", "Isla", "Ethan", "Zara", "Lucas", "Ava",
         "Mason", "Chloe", "Oliver", "Priya", "Jack", "Emma", "Kai", "Lily", "Ryan", "Maya"]
LAST = ["Sharma", "Nguyen", "Smith", "Chen", "Patel", "Brown", "Kim", "Wilson", "Lee", "Taylor",
        "Singh", "Jones", "Wang", "Davis", "Khan", "Clark", "Tran", "Evans", "Gupta", "Moore"]
# attendance habit: chance of showing up to any given class
HABITS = [0.97] * 8 + [0.85] * 5 + [0.65] * 4 + [0.35] * 3


def seed(db, now=None, weeks=8, seed_value=31275):
    rnd = random.Random(seed_value)
    now = now or time.time()
    for t in ("taps", "sessions", "students", "warnings"):
        db.execute(f"DELETE FROM {t}")
    db.commit()

    students = []
    for i in range(20):
        sid = str(14000000 + rnd.randint(1000, 9999) * 10 + i)
        uid = str(rnd.randint(10**9, 10**10 - 1))
        name = f"{FIRST[i]} {LAST[(i * 7) % 20]}"
        db.execute("INSERT INTO students VALUES (?,?,?,?)",
                   (uid, sid, name, f"{FIRST[i].lower()}.{LAST[(i * 7) % 20].lower()}@student.example.edu"))
        students.append((uid, HABITS[i]))
    db.commit()

    length = 2 * 3600
    live_start = now - 40 * 60  # the current class started 40 minutes ago
    ids = []
    for k in range(weeks, -1, -1):
        start = live_start - k * 7 * 86400
        cur = db.execute("INSERT INTO sessions(name, start_ts, end_ts) VALUES (?,?,?)",
                         (f"Wk {weeks - k + 1} Lecture", start, start + length))
        ids.append((cur.lastrowid, start, k))
    db.commit()

    for sid, start, k in ids:
        for uid, habit in students:
            if rnd.random() > habit:
                continue
            t_in = start + rnd.choice([rnd.uniform(-600, 300)] * 3 + [rnd.uniform(600, 1800)])
            if k == 0 and t_in > now - 60:      # live class: only taps that already happened
                continue
            logic.handle_tap(db, uid, t_in)
            if rnd.random() < 0.08:              # double tap a few seconds later
                logic.handle_tap(db, uid, t_in + rnd.uniform(2, 6))
            if k == 0:
                continue                         # live class: nobody has left yet, bar a few below
            r = rnd.random()
            if r < 0.10:
                continue                         # forgot to tap out
            t_out = start + length + rnd.uniform(-300, 600) if r > 0.18 else start + length * rnd.uniform(0.3, 0.6)
            logic.handle_tap(db, uid, max(t_out, t_in + 20))

    # an unregistered card so the exceptions panel is not empty on first load
    logic.handle_tap(db, "9999999999", now - 600)
    db.commit()


if __name__ == "__main__":
    db = logic.connect()
    seed(db)
    print("Seeded", db.execute("SELECT COUNT(*) FROM students").fetchone()[0], "students,",
          db.execute("SELECT COUNT(*) FROM taps").fetchone()[0], "taps ->", logic.DB_PATH)
