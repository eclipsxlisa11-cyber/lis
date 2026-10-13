"""Scripted test from the methodology slide: clean taps plus the cheats, then the four numbers.
Run: python test_tapin.py"""
import os, tempfile, unittest

import logic


class TapInTest(unittest.TestCase):
    def setUp(self):
        self.db = logic.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.t0 = 1_800_000_000.0
        self.db.execute("INSERT INTO sessions(name,start_ts,end_ts) VALUES ('S', ?, ?)", (self.t0, self.t0 + 7200))
        for i in range(10):
            self.db.execute("INSERT INTO students VALUES (?,?,?,?)", (f"c{i}", f"id{i}", f"Student {i}", None))
        self.db.commit()

    def tap(self, uid, dt):
        return logic.handle_tap(self.db, uid, self.t0 + dt)

    def test_in_out_alternate_and_dwell(self):
        self.assertEqual(self.tap("c0", 60)["result"], "IN")
        self.assertEqual(self.tap("c0", 7000)["result"], "OUT")
        row = [r for r in logic.session_rows(self.db, self.db.execute("SELECT * FROM sessions").fetchone(), self.t0 + 9000) if r["uid"] == "c0"][0]
        self.assertEqual(row["status"], "COMPLETE"); self.assertEqual(round(row["dwell"]), 6940)

    def test_double_tap_blocked_and_not_counted(self):
        self.tap("c1", 60)
        self.assertEqual(self.tap("c1", 63)["result"], "DUPLICATE")
        self.assertEqual(self.tap("c1", 4000)["result"], "OUT")   # the double did not flip the state

    def test_missing_tap_out_and_early_leaver(self):
        self.tap("c2", 60)                                         # never taps out
        self.tap("c3", 60); self.tap("c3", 1200)                   # leaves after 19 minutes
        s = self.db.execute("SELECT * FROM sessions").fetchone()
        st = {r["uid"]: r["status"] for r in logic.session_rows(self.db, s, self.t0 + 7200 + 3600)}
        self.assertEqual(st["c2"], "MISSING_OUT"); self.assertEqual(st["c3"], "EARLY_LEAVE"); self.assertEqual(st["c4"], "ABSENT")

    def test_unknown_card_and_no_class(self):
        self.assertEqual(self.tap("zzz", 60)["result"], "UNKNOWN")
        self.assertEqual(self.tap("c0", -86400)["result"], "NO_SESSION")

    def test_late_flag_and_zones(self):
        self.assertIn("late", self.tap("c5", 1500)["message"])
        self.assertEqual((logic.zone_of(85), logic.zone_of(70), logic.zone_of(40), logic.zone_of(None)), ("GREEN", "AMBER", "RED", "NONE"))

    def test_report_numbers(self):
        cheats = [("c6", 60, "DUPLICATE", 63), ("c7", 60, "DUPLICATE", 65), ("c8", 60, "DUPLICATE", 68)]
        caught = 0
        for uid, t, want, t2 in cheats:
            self.tap(uid, t); caught += self.tap(uid, t2)["result"] == want
        clean = [self.tap(f"c{i}", 100)["flag"] for i in (0, 1, 2, 3, 4, 5, 9)]
        print(f"\nCatch rate {caught}/{len(cheats)} | False flags {sum(clean)}/{len(clean)}")
        self.assertEqual(caught, len(cheats)); self.assertEqual(sum(clean), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
