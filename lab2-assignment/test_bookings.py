"""HTTPX client that tests the running booking service with real HTTP requests.

Usage:  python test_bookings.py [base_url]     (default http://127.0.0.1:8000)
Exit code is 0 if every test passes, 1 otherwise.
Each run uses unique room and booking IDs, so it can be repeated on one server.
"""
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=5)
run = uuid.uuid4().hex[:6]          # unique per run
ROOM, ROOM2 = f"B{run}", f"C{run}"
results = []


def check(desc, method, uri, expected, json=None, ok=None):
    """Send a real request, compare status (and optionally the payload)."""
    r = client.request(method, uri, json=json)
    passed = r.status_code == expected
    if passed and ok is not None:
        try:
            passed = bool(ok(r))
        except Exception:
            passed = False
    results.append(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {desc}\n"
          f"       {method} {uri} | actual {r.status_code} | expected {expected}")
    if not passed:
        print(f"       body: {r.text[:200]}")
    return r


def booking(room=ROOM, date="2030-05-10", start=10, end=12, purpose="Lecture"):
    return {"room_id": room, "date": date, "start_hour": start,
            "end_hour": end, "purpose": purpose}


def count_bookings():
    return len(client.get("/bookings").json())


print(f"Testing {BASE} (run id {run})\n")

# ---- Rooms ----
room = {"room_id": ROOM, "building": "Engineering", "capacity": 40}
check("1. Create valid room", "POST", "/rooms", 201, room,
      ok=lambda r: r.json() == room)
check("1b. Create second room (for other-room test)", "POST", "/rooms", 201,
      {"room_id": ROOM2, "building": "Science", "capacity": 25})
check("2. List rooms", "GET", "/rooms", 200,
      ok=lambda r: any(x["room_id"] == ROOM for x in r.json()))
check("3. Retrieve existing room", "GET", f"/rooms/{ROOM}", 200,
      ok=lambda r: r.json()["capacity"] == 40)
check("4. Retrieve unknown room", "GET", f"/rooms/NOPE{run}", 404)
check("5. Create duplicate room", "POST", "/rooms", 409, room)
check("6. Invalid room capacity (0)", "POST", "/rooms", 422,
      {"room_id": f"X{run}", "building": "Eng", "capacity": 0})
check("6b. Empty room ID", "POST", "/rooms", 422,
      {"room_id": "  ", "building": "Eng", "capacity": 10})
check("6c. Empty building", "POST", "/rooms", 422,
      {"room_id": f"Y{run}", "building": "", "capacity": 10})

# ---- Bookings: create / read ----
b1 = f"bk-{run}-1"
check("7. Create valid booking (10-12)", "PUT", f"/bookings/{b1}", 201, booking(),
      ok=lambda r: r.json()["booking_id"] == b1)
check("8. Retrieve existing booking", "GET", f"/bookings/{b1}", 200,
      ok=lambda r: r.json()["purpose"] == "Lecture")
check("8b. List bookings", "GET", "/bookings", 200,
      ok=lambda r: any(x["booking_id"] == b1 for x in r.json()))
check("9. Retrieve unknown booking", "GET", f"/bookings/nope-{run}", 404)
check("10. Booking for unknown room", "PUT", f"/bookings/bk-{run}-unk", 404,
      booking(room=f"NOPE{run}"))

# ---- Overlap rules (existing: ROOM, 2030-05-10, 10-12) ----
check("11. Overlapping booking (11-13)", "PUT", f"/bookings/bk-{run}-ov1", 409,
      booking(start=11, end=13))
check("11b. Same slot, different ID (10-12)", "PUT", f"/bookings/bk-{run}-ov2", 409,
      booking())
check("11c. Fully inside existing (10-11)", "PUT", f"/bookings/bk-{run}-ov3", 409,
      booking(start=10, end=11))
check("12. Adjacent booking after (12-13)", "PUT", f"/bookings/bk-{run}-adj1", 201,
      booking(start=12, end=13))
check("12b. Adjacent booking before (08-10)", "PUT", f"/bookings/bk-{run}-adj2", 201,
      booking(start=8, end=10))
check("13. Same time, different room", "PUT", f"/bookings/bk-{run}-room2", 201,
      booking(room=ROOM2))
check("14. Same time, different date", "PUT", f"/bookings/bk-{run}-date2", 201,
      booking(date="2030-05-11"))

# ---- Validation (all 422) ----
check("15. start_hour >= end_hour", "PUT", f"/bookings/bk-{run}-v1", 422,
      booking(start=14, end=14))
check("15b. start_hour after end_hour", "PUT", f"/bookings/bk-{run}-v1b", 422,
      booking(start=15, end=13))
check("16. Start before 08:00", "PUT", f"/bookings/bk-{run}-v2", 422,
      booking(start=7, end=9))
check("17. End after 20:00", "PUT", f"/bookings/bk-{run}-v3", 422,
      booking(start=19, end=21))
check("18. Empty purpose", "PUT", f"/bookings/bk-{run}-v4", 422,
      booking(purpose="   "))
missing = booking(); del missing["end_hour"]
check("19. Missing required field (end_hour)", "PUT", f"/bookings/bk-{run}-v5", 422,
      missing)
check("20. Wrong field type (start_hour = 'ten')", "PUT", f"/bookings/bk-{run}-v6", 422,
      {**booking(), "start_hour": "ten"})
check("20b. Invalid date", "PUT", f"/bookings/bk-{run}-v7", 422,
      booking(date="2030-02-31"))

# ---- Idempotent PUT (Requirement 4) ----
b2 = f"bk-{run}-idem"
slot = booking(date="2030-06-01", start=14, end=16, purpose="Exam")
check("A. First PUT with new ID creates booking", "PUT", f"/bookings/{b2}", 201, slot)
before = count_bookings()
r = check("B. Retry identical PUT (same ID, same payload)", "PUT", f"/bookings/{b2}", 200,
          slot, ok=lambda r: r.json()["booking_id"] == b2)
after = count_bookings()
results.append(before == after)
print(f"[{'PASS' if before == after else 'FAIL'}] B2. Booking count unchanged by retry\n"
      f"       GET /bookings | count before {before} | count after {after}")
check("C. Same ID, different details", "PUT", f"/bookings/{b2}", 409,
      {**slot, "purpose": "Changed"})
check("D. Different ID, overlapping slot", "PUT", f"/bookings/{b2}-other", 409, slot)
check("D2. Original booking still unchanged", "GET", f"/bookings/{b2}", 200,
      ok=lambda r: r.json()["purpose"] == "Exam")

# ---- Delete ----
check("21. Delete existing booking", "DELETE", f"/bookings/{b2}", 204)
check("21b. Deleted booking is gone", "GET", f"/bookings/{b2}", 404)
check("21c. Slot is free again after delete", "PUT", f"/bookings/{b2}-reuse", 201, slot)
check("22. Delete unknown booking", "DELETE", f"/bookings/nope-{run}", 404)


# ---- Concurrency (single server process) ----
def concurrent_puts(items):
    """Send all PUTs at (nearly) the same moment from separate threads."""
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        futures = [pool.submit(httpx.put, f"{BASE}/bookings/{bid}", json=body, timeout=10)
                   for bid, body in items]
        return [f.result() for f in futures]


race_slot = booking(date="2030-07-01", start=9, end=11)
codes = sorted(x.status_code for x in concurrent_puts(
    [(f"bk-{run}-race{i}", race_slot) for i in range(10)]))
passed = codes == [201] + [409] * 9
results.append(passed)
print(f"[{'PASS' if passed else 'FAIL'}] 23. 10 concurrent PUTs, different IDs, same slot\n"
      f"       statuses {codes} | expected exactly one 201 and nine 409")
in_slot = [x for x in client.get("/bookings").json()
           if x["room_id"] == ROOM and x["date"] == "2030-07-01"]
passed = len(in_slot) == 1
results.append(passed)
print(f"[{'PASS' if passed else 'FAIL'}] 23b. Only one booking stored for that slot\n"
      f"       GET /bookings | stored {len(in_slot)} | expected 1")

same_id = [(f"bk-{run}-same", booking(date="2030-08-01"))] * 10
codes = sorted(x.status_code for x in concurrent_puts(same_id))
passed = codes == [200] * 9 + [201]
results.append(passed)
print(f"[{'PASS' if passed else 'FAIL'}] 24. 10 concurrent identical PUTs, same ID\n"
      f"       statuses {codes} | expected one 201 and nine 200")
stored = [x for x in client.get("/bookings").json() if x["booking_id"] == f"bk-{run}-same"]
passed = len(stored) == 1
results.append(passed)
print(f"[{'PASS' if passed else 'FAIL'}] 24b. Only one booking stored for that ID\n"
      f"       GET /bookings | stored {len(stored)} | expected 1")

# ---- Summary ----
total, ok_count = len(results), sum(results)
print(f"\nTotal: {total} | Passed: {ok_count} | Failed: {total - ok_count}")
sys.exit(0 if ok_count == total else 1)
