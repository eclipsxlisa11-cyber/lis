# TapIn: RFID attendance dashboard

Raspberry Pi + RC522 reader. Students tap a card in and out; the Pi stores every tap in SQLite and serves a lecturer dashboard.

## Run

    pip install flask                 # on the Pi also: pip install mfrc522 RPi.GPIO
    python seed.py                    # optional: 20 demo students, 8 past classes + 1 live
    python app.py                     # dashboard at http://<pi-ip>:5000
    python reader.py                  # RFID loop (types a UID instead if no RC522 is attached)
    python test_tapin.py              # scripted test: catch rate, false flags

No internet needed on the Pi: Chart.js is bundled in `static/`.

## What the dashboard shows

| Feature | Where |
|---|---|
| Student name, student ID, tap-in time, tap-out time, time on site | Attendance register |
| Double taps (second tap within 10 s is blocked, logged, never flips in/out) | Exceptions + register tag |
| Tap in with no tap out (flagged once the class closes, 30 min after the end) | Exceptions + register |
| Early leavers (stayed under 75% of the class), late arrivals (after 10 min) | Register + Exceptions |
| Unknown cards, taps outside a class window | Exceptions |
| Green / amber / red attendance zones, per-student bar chart, class turnout trend | Attendance zones |
| Warnings: one click or "Warn all" for the red zone, 7-day cooldown, history | Red zone box + Warnings sent |
| Warning shown at the tap itself for red-zone students (`warning` field, on screen / reader) | Tap response |
| CSV export of any session | Register header |

## Policy (edit the constants at the top of `logic.py`)

- Green >= 80%, red < 60%, amber between.
- Per class: tap in + tap out = 1 credit; tap in only or early leave = 0.5; absent = 0.
- Attendance % is calculated over closed classes only, so a student is not penalised mid-class.

## Limits to state in the report

- Anti-passback here is timing based (10 s) plus state tracking. It cannot detect a friend tapping someone else's card minutes later.
- The dashboard and `/api/tap` have no login. Fine on an isolated lab network; add authentication before real use.
- Warnings are emailed only if `SMTP_HOST` (and optionally `SMTP_PORT`, `SMTP_FROM`) are set; otherwise they are logged to the database.
- Reader GPIO pins for the buzzer and LEDs (physical pins 32, 36, 12) are placeholders: match them to your wiring.
