"""Integration tests for the Exam Scheduler Service (real processes, real HTTP).

Usage:  python test_scheduler.py
The script starts and stops the services itself (ports 8004-8017) and exits
with code 0 only if every test passes.
"""
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

import harness
import run_experiments
import scheduler_service
from harness import (BOOKING_URL, SCHEDULER_URL, booking_service, bookings_for, exam,
                     post_exam, scheduler_service as start_scheduler, seed_rooms)

results = []


def check(desc, actual, expected):
    passed = actual == expected
    results.append(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {desc}\n       actual: {actual!r} | expected: {expected!r}")


def section(title):
    print(f"\n--- {title} ---")


def get(path):
    return httpx.get(f"{BOOKING_URL}{path}", timeout=10)


def total_bookings():
    return len(get("/bookings").json())


def run_tests():
    workdir = tempfile.mkdtemp(prefix="lab3-test-")
    db = os.path.join(workdir, "tests.db")

    # ------------------------------------------------------------------
    section("Setup: two services on separate ports")
    booking = booking_service(db).start()
    seed_rooms()
    sched = start_scheduler(timeout=1.0, retries=2, base=0.1).start()
    check("Booking service answers on port 8005",
          httpx.get(f"{BOOKING_URL}/health").status_code, 200)
    check("Scheduler answers on a different port (8004)",
          httpx.get(f"{SCHEDULER_URL}/config").status_code, 200)

    # ------------------------------------------------------------------
    section("A. Scheduling (rooms: R20=20, R40a=40, R40b=40, R100=100 seats)")
    d = "2031-01-10"
    r = post_exam(exam("s1", d, students=30))
    check("Valid exam returns 201", r.status_code, 201)
    check("Smallest suitable room chosen (30 students -> R40a, tie broken by room ID)",
          r.json()["room_id"], "R40a")
    check("Stable booking ID is exam-s1", r.json()["booking_id"], "exam-s1")
    check("Booking really exists in the booking service",
          get("/bookings/exam-s1").json()["room_id"], "R40a")

    r = post_exam(exam("s2", d, students=30))
    check("Occupied room R40a is skipped -> R40b", (r.status_code, r.json()["room_id"]),
          (201, "R40b"))
    r = post_exam(exam("s3", d, students=30))
    check("Larger free room chosen when smaller ones are taken -> R100",
          (r.status_code, r.json()["room_id"]), (201, "R100"))
    before = total_bookings()
    r = post_exam(exam("s4", d, students=30))
    check("No free eligible room -> 409", r.status_code, 409)
    check("No booking was created for the failed exam", total_bookings(), before)
    r = post_exam(exam("s5", d, students=500))
    check("No sufficiently large room -> 409", r.status_code, 409)
    check("... with a helpful message", "large enough" in r.json()["detail"], True)
    r = post_exam(exam("s6", "2031-01-11", students=15))
    check("15 students -> smallest room R20", (r.status_code, r.json()["room_id"]),
          (201, "R20"))
    r = post_exam(exam("s7", d, start=11, end=13, students=30))
    check("Partly overlapping time -> every eligible room clashes -> 409", r.status_code, 409)
    r = post_exam(exam("s8", d, start=12, end=14, students=30))
    check("Adjacent time slot (12-14) is allowed -> R40a",
          (r.status_code, r.json()["room_id"]), (201, "R40a"))

    base = exam("v", "2031-01-20")
    invalid = {
        "start hour before 08:00": {**base, "start_hour": 7},
        "end hour after 20:00": {**base, "end_hour": 21},
        "start >= end": {**base, "start_hour": 12, "end_hour": 12},
        "zero students": {**base, "num_students": 0},
        "empty course code": {**base, "course_code": "  "},
        "empty exam id": {**base, "exam_id": ""},
        "invalid date": {**base, "date": "2031-02-30"},
        "wrong type (num_students='many')": {**base, "num_students": "many"},
        "missing field (date)": {k: v for k, v in base.items() if k != "date"},
    }
    for name, body in invalid.items():
        check(f"Invalid exam ({name}) -> 422", post_exam(body).status_code, 422)

    # ------------------------------------------------------------------
    section("B. Configuration")
    check("GET /config shows the active settings", httpx.get(f"{SCHEDULER_URL}/config").json(), {
        "room_booking_service_url": "http://127.0.0.1:8005", "request_timeout_seconds": 1.0,
        "max_retries": 2, "backoff_base_seconds": 0.1})
    bad = [{"REQUEST_TIMEOUT_SECONDS": "0"}, {"REQUEST_TIMEOUT_SECONDS": "fast"},
           {"MAX_RETRIES": "-1"}, {"MAX_RETRIES": "2.5"}, {"ROOM_BOOKING_SERVICE_URL": "localhost"},
           {"BACKOFF_BASE_SECONDS": "-1"}]
    for env in bad:
        try:
            scheduler_service.load_settings(env)
            outcome = "accepted"
        except ValueError:
            outcome = "rejected"
        check(f"Invalid setting {env} is rejected", outcome, "rejected")
    check("Defaults are applied when nothing is set",
          scheduler_service.load_settings({}),
          scheduler_service.Settings("http://127.0.0.1:8005", 1.0, 3, 0.5))

    # ------------------------------------------------------------------
    section("C. Retry policy")
    check("Backoff intervals for base 0.5 s",
          [scheduler_service.backoff_delay(n, 0.5) for n in (1, 2, 3)], [0.5, 1.0, 2.0])

    dead = start_scheduler(timeout=0.5, retries=2, base=0.1, booking_url="http://127.0.0.1:8099",
                           port=8011).start()
    start = time.monotonic()
    r = post_exam(exam("r1", "2031-02-01"), "http://127.0.0.1:8011")
    elapsed = time.monotonic() - start
    check("Connection failure: retried, then 503", (r.status_code, r.json()["retries_used"]),
          (503, 2))
    log = dead.log_text()
    check("... 3 attempts logged with exponential backoff",
          ("attempt 1/3 failed" in log and "attempt 3/3 failed" in log
           and "backoff 0.10s" in log and "backoff 0.20s" in log), True)
    check("... total time includes the backoff waits (>= 0.3 s)", elapsed >= 0.3, True)

    slow_db = os.path.join(workdir, "slow.db")
    slow = booking_service(slow_db, delay=1.0, port=8006).start()
    timeout_sched = start_scheduler(timeout=0.3, retries=1, base=0.1,
                                    booking_url="http://127.0.0.1:8006", port=8012).start()
    r = post_exam(exam("r2", "2031-02-02"), "http://127.0.0.1:8012")
    check("Timeout: retried, then 503", (r.status_code, r.json()["retries_used"]), (503, 1))
    check("... log shows a timeout failure", "ReadTimeout" in timeout_sched.log_text(), True)

    stub_calls = {}
    for mode in ("500", "404", "409"):
        port = {"500": 8013, "404": 8015, "409": 8016}[mode]
        stub = harness.Service("stub_service", port, {"STUB_MODE": mode}, "/_hits").start()
        sch = start_scheduler(timeout=0.5, retries=2, base=0.1,
                              booking_url=f"http://127.0.0.1:{port}", port=port + 100).start()
        r = post_exam(exam("r3", "2031-02-03"), f"http://127.0.0.1:{port + 100}")
        stub_calls[mode] = (r, httpx.get(f"http://127.0.0.1:{port}/_hits").json(), sch)

    r, hits, sch = stub_calls["500"]
    check("HTTP 500: retried, then 503", (r.status_code, r.json()["retries_used"]), (503, 2))
    check("... stub received 3 identical requests (1 + 2 retries)",
          hits.get("GET /bookings/exam-r3"), 3)
    check("... backoff doubles in the log", "backoff 0.10s" in sch.log_text()
          and "backoff 0.20s" in sch.log_text(), True)
    r, hits, sch = stub_calls["404"]
    check("HTTP 404 is not retried (PUT sent once, retries_used 0)",
          (hits.get("PUT /bookings/exam-r3"), r.json()["retries_used"]), (1, 0))
    check("... room skipped, no room left -> 409", r.status_code, 409)
    r, hits, sch = stub_calls["409"]
    check("HTTP 409 is not retried (PUT sent once, retries_used 0)",
          (hits.get("PUT /bookings/exam-r3"), r.json()["retries_used"]), (1, 0))
    check("... no free room -> 409 (not 503)", r.status_code, 409)

    # ------------------------------------------------------------------
    section("D. Idempotency")
    before = total_bookings()
    r = post_exam(exam("s1", d, students=30))
    check("Identical repeat of exam s1 returns 200 with the same booking",
          (r.status_code, r.json()["room_id"], r.json()["booking_id"]),
          (200, "R40a", "exam-s1"))
    check("... no second booking created", (len(bookings_for("s1")), total_bookings()),
          (1, before))
    r = post_exam(exam("s1", d, students=35))
    check("Same exam ID with different details -> 409", r.status_code, 409)
    r = post_exam(exam("s1", "2031-01-15", students=30))
    check("Same exam ID on a different date -> 409", r.status_code, 409)
    check("... original booking untouched, still one booking",
          (len(bookings_for("s1")), get("/bookings/exam-s1").json()["date"]), (1, d))

    with ThreadPoolExecutor(max_workers=10) as pool:
        replies = list(pool.map(lambda _: post_exam(exam("c1", "2031-01-12")), range(10)))
    check("10 concurrent identical requests: one 201, nine 200",
          sorted(x.status_code for x in replies), [200] * 9 + [201])
    check("... all agree on the room", len({x.json()["room_id"] for x in replies}), 1)
    check("... exactly one booking stored", len(bookings_for("c1")), 1)

    # Lost/late response: the booking is committed, but the reply arrives too late.
    booking.stop()
    booking = booking_service(db, delay=1.0, delay_methods="PUT").start()
    lost = start_scheduler(timeout=0.4, retries=1, base=0.1, port=8017).start()
    r = post_exam(exam("lost1", "2031-01-13"), "http://127.0.0.1:8017")
    check("Reply too slow: scheduler answers 503 (result unknown)", r.status_code, 503)
    log = lost.log_text()
    check("... it retried the same booking ID (2 PUT attempts) and never tried another room",
          (log.count("PUT /bookings/exam-lost1 attempt"), "trying the next one" in log), (2, False))
    time.sleep(1.5)   # let the slow service finish its in-flight replies
    exists = get("/bookings/exam-lost1")
    check("Booking was committed on the server despite the 503",
          (exists.status_code, len(bookings_for("lost1"))), (200, 1))
    booking.stop()
    booking = booking_service(db).start()     # restart: data survives (SQLite)
    check("Booking survives a service restart", get("/bookings/exam-lost1").status_code, 200)
    r = post_exam(exam("lost1", "2031-01-13"))
    check("Repeating the same request finds the booking: 200, no duplicate",
          (r.status_code, len(bookings_for("lost1"))), (200, 1))

    harness.stop_all()

    # ------------------------------------------------------------------
    section("E. Integration: outage recovery and delay experiments (real processes)")
    rec = run_experiments.recovery_experiment(workdir)
    check("Request sent during an outage succeeds after the restart (201)", rec["status"], 201)
    check("... retries were used", rec["body"]["retries_used"] >= 1, True)
    check("... log shows failed attempts then success",
          "failed" in rec["log"] and "-> HTTP 201" in rec["log"], True)
    check("... exactly one booking exists", rec["bookings"], 1)
    check("Identical repeat after recovery: 200, still one booking",
          (rec["repeat_status"], rec["bookings_after_repeat"]), (200, 1))

    rows = {r["name"]: r for r in run_experiments.delay_experiments(workdir)}
    check("Experiment A (delay 0.1, timeout 1.0): 201 without retries",
          (rows["A"]["status"], rows["A"]["retries"]), (201, 0))
    check("Experiment B (delay 0.8, timeout 1.5): 201 without retries, slower than A",
          (rows["B"]["status"], rows["B"]["retries"], rows["B"]["ms"] > rows["A"]["ms"]),
          (201, 0, True))
    check("Experiment C (delay 1.5, timeout 0.5): retries exhausted -> 503",
          (rows["C"]["status"], rows["C"]["retries"]), (503, 3))
    check("Experiment D (only PUT slow): 503 although the booking exists",
          (rows["D"]["status"], rows["D"]["booking_exists"]), (503, True))
    check("... repeating it later returns 200, still one booking",
          (rows["D"]["follow_up_status"], rows["D"]["follow_up_bookings"]), (200, 1))


if __name__ == "__main__":
    try:
        run_tests()
    finally:
        harness.stop_all()
    passed = sum(results)
    print(f"\nTotal tests: {len(results)} | Passed: {passed} | Failed: {len(results) - passed}")
    sys.exit(0 if passed == len(results) else 1)
