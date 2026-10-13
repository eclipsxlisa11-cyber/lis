"""Raspberry Pi card reader loop. Run: python reader.py
Reads cards with the RC522 and passes each one to logic.handle_tap().
Without the hardware libraries it falls back to typing a card UID in the terminal."""
import time

import logic

try:
    from mfrc522 import SimpleMFRC522
    import RPi.GPIO as GPIO
    reader, HW = SimpleMFRC522(), True
    BUZZER, LED_OK, LED_BAD = 12, 16, 18   # BCM pins: change to match your wiring
    GPIO.setmode(GPIO.BCM)
    for p in (BUZZER, LED_OK, LED_BAD):
        GPIO.setup(p, GPIO.OUT)
except ImportError:
    HW = False


def signal(result):
    """One short beep + green LED = accepted, three beeps + red LED = flagged."""
    ok = result in ("IN", "OUT")
    if not HW:
        return
    pin, beeps = (LED_OK, 1) if ok else (LED_BAD, 3)
    for _ in range(beeps):
        GPIO.output((pin, BUZZER), 1); time.sleep(0.12)
        GPIO.output((pin, BUZZER), 0); time.sleep(0.08)


def main():
    db = logic.connect()
    print("TapIn reader ready." + ("" if HW else " (no RC522 found: type a card UID instead)"))
    while True:
        uid = reader.read()[0] if HW else input("card uid> ").strip()
        if not uid:
            continue
        res = logic.handle_tap(db, uid, time.time())
        print(time.strftime("%H:%M:%S"), res["result"], res["message"], res.get("warning", ""))
        signal(res["result"])
        time.sleep(1.0)  # card stays in the field after a read; wait before the next one


if __name__ == "__main__":
    main()
