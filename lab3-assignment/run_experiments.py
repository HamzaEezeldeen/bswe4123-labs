"""Real experiments: (1) stop/restart of the booking service, (2) delay/timeout table.

Usage:  python run_experiments.py      (writes experiment_output.txt)
Everything runs with real processes and real HTTP requests; nothing is simulated.
"""
import os
import tempfile
import threading
import time

import httpx

import harness
from harness import (BOOKING_URL, bookings_for, booking_service, exam, post_exam,
                     scheduler_service, seed_rooms)


def retry_lines(text):
    return [line for line in text.splitlines() if "attempt" in line or "exam" in line]


def recovery_experiment(workdir):
    """Stop the booking service, send POST /exams, restart it mid-retry."""
    db = os.path.join(workdir, "recovery.db")
    booking = harness.booking_service(db).start()
    seed_rooms()
    scheduler = scheduler_service(timeout=1.0, retries=5, base=0.5).start()
    events = []
    t0 = time.monotonic()

    def note(text):
        events.append(f"[t=+{time.monotonic() - t0:4.1f}s] {text}")

    booking.stop()
    note("Room Booking Service STOPPED")
    result = {}

    def send():
        note("POST /exams sent (booking service is down)")
        start = time.monotonic()
        r = post_exam(exam("recover1", "2031-03-01"))
        result["elapsed"] = time.monotonic() - start
        result["response"] = r
        note(f"POST /exams answered HTTP {r.status_code}")

    sender = threading.Thread(target=send)
    sender.start()
    time.sleep(3.0)
    note("Room Booking Service restart requested")
    booking = harness.booking_service(db).start()    # returns once it answers /health
    note("Room Booking Service is up again (answering /health)")
    sender.join()

    r = result["response"]
    count = len(bookings_for("recover1"))
    repeat = post_exam(exam("recover1", "2031-03-01"))
    count_after = len(bookings_for("recover1"))
    out = {"status": r.status_code, "body": r.json(), "elapsed": result["elapsed"],
           "events": events, "log": scheduler.log_text(), "bookings": count,
           "repeat_status": repeat.status_code, "repeat_body": repeat.json(),
           "bookings_after_repeat": count_after}
    booking.stop()
    scheduler.stop()
    return out


# (name, delay seconds, delayed methods, scheduler timeout seconds)
EXPERIMENTS = [
    ("A", 0.1, "GET,POST,PUT,DELETE", 1.0),
    ("B", 0.8, "GET,POST,PUT,DELETE", 1.5),
    ("C", 1.5, "GET,POST,PUT,DELETE", 0.5),
    ("D", 1.5, "PUT", 0.5),      # only the booking call is slow: the 'lost response' case
]


def delay_experiments(workdir):
    """Each experiment restarts both services with new environment variables."""
    db = os.path.join(workdir, "delay.db")
    rows = []
    for number, (name, delay, methods, timeout) in enumerate(EXPERIMENTS, start=1):
        booking = harness.booking_service(db, delay, methods).start()
        seed_rooms()   # rooms already exist after the first call (409 is fine)
        scheduler = scheduler_service(timeout=timeout, retries=3, base=0.5).start()
        body = exam(f"exp-{name}", f"2031-04-0{number}")

        start = time.monotonic()
        r = post_exam(body)
        elapsed_ms = (time.monotonic() - start) * 1000
        payload = r.json()

        # Look at the database directly (slow service: use a long timeout).
        exists = httpx.get(f"{BOOKING_URL}/bookings/exam-exp-{name}", timeout=10).status_code == 200
        row = {"name": name, "delay": delay, "methods": methods, "timeout": timeout,
               "status": r.status_code, "ms": elapsed_ms, "retries": payload["retries_used"],
               "booking_exists": exists, "log": scheduler.log_text()}
        if r.status_code == 201:
            row["observation"] = f"Booked in {payload['room_id']}; " + (
                "no retries needed" if row["retries"] == 0 else f"{row['retries']} retries")
        else:
            row["observation"] = (f"Gave up after {row['retries']} retries; booking "
                                  + ("EXISTS on the server (reply was too slow)" if exists
                                     else "was not created"))
        scheduler.stop()
        booking.stop()

        if name == "D":   # recover: same request again, service no longer slow
            booking = harness.booking_service(db).start()
            scheduler = scheduler_service(timeout=timeout, retries=3, base=0.5).start()
            repeat = post_exam(body)
            row["follow_up_status"] = repeat.status_code
            row["follow_up_bookings"] = len(bookings_for(f"exp-{name}"))
            scheduler.stop()
            booking.stop()
        rows.append(row)
    return rows


def format_table(rows):
    lines = ["| Exp | Delay (s) | Timeout (s) | HTTP Status | Response Time (ms) | Retries | Observation |",
             "|-----|-----------|-------------|-------------|--------------------|---------|-------------|"]
    for r in rows:
        delayed = "" if "GET" in r["methods"] else " (PUT only)"
        lines.append(f"| {r['name']} | {r['delay']}{delayed} | {r['timeout']} | {r['status']} | "
                     f"{r['ms']:.0f} | {r['retries']} | {r['observation']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    out = []

    def show(text=""):
        print(text, flush=True)
        out.append(text)

    workdir = tempfile.mkdtemp(prefix="lab3-exp-")
    try:
        show("=== Experiment 1: stop and restart the Room Booking Service ===")
        rec = recovery_experiment(workdir)
        for event in rec["events"]:
            show(event)
        show("\nScheduler log (retry lines):")
        for line in retry_lines(rec["log"]):
            show(line)
        show(f"\nPOST /exams -> HTTP {rec['status']} after {rec['elapsed']:.2f} s, "
             f"retries_used={rec['body'].get('retries_used')}, room={rec['body'].get('room_id')}")
        show(f"Bookings stored for exam 'recover1': {rec['bookings']}")
        show(f"Identical repeat -> HTTP {rec['repeat_status']}; bookings stored now: "
             f"{rec['bookings_after_repeat']}")

        show("\n=== Experiment 2: delay / timeout combinations (MAX_RETRIES=3, backoff base 0.5 s) ===")
        rows = delay_experiments(workdir)
        show(format_table(rows))
        for r in rows:
            if "follow_up_status" in r:
                show(f"\nFollow-up for {r['name']}: same request, service no longer slow -> "
                     f"HTTP {r['follow_up_status']}, bookings stored for that exam: "
                     f"{r['follow_up_bookings']}")
        show("\nScheduler log of experiment C (timeouts and backoff):")
        for line in retry_lines(next(r for r in rows if r["name"] == "C")["log"]):
            show(line)
    finally:
        harness.stop_all()
    with open(os.path.join(harness.HERE, "experiment_output.txt"), "w") as f:
        f.write("\n".join(out) + "\n")
