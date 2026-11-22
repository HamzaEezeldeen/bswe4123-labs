# Lab Assignment 2: A Campus Room Booking Service

- **University:** University of Palestine
- **Course:** Distributed Systems Lab (BSWE4123)
- **Student:** Hamza Ezzeldeen Mustafa Elkhatib
- **Student ID:** 120231615

## Overview

A small REST service for booking campus rooms, plus an HTTPX client that tests it with real HTTP requests. The service stores rooms and bookings in memory, rejects invalid input (422), unknown resources (404) and overlapping or conflicting bookings (409), and supports retry-safe booking creation with an idempotent `PUT`.

> The assignment PDF was not available in the workspace, so the implementation follows the written specification supplied with the task.

## Technologies

Python 3, FastAPI, Pydantic v2, Uvicorn, HTTPX.

## Setup (Windows PowerShell)

```powershell
cd lab2-assignment
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Start the server

```powershell
.\.venv\Scripts\python -m uvicorn service:app --port 8000
```

Interactive API docs: http://127.0.0.1:8000/docs

## Run the tests

With the server running, open a second PowerShell window:

```powershell
cd lab2-assignment
.\.venv\Scripts\python test_bookings.py
```

An optional argument sets another base URL. The script prints every request, exits with code 0 if all tests pass and 1 otherwise. Each run uses unique room and booking IDs, so it can be repeated against the same server.

## REST API design

Error responses are JSON: `{"detail": "..."}` for 404/409, and FastAPI's list of field errors for 422.

| # | Method | URI | Request body | Success | Errors |
|---|--------|-----|--------------|---------|--------|
| 1 | GET | `/rooms` | none | 200 OK (list) | none |
| 2 | POST | `/rooms` | `{room_id, building, capacity}` | 201 Created | 409 room ID exists; 422 invalid body |
| 3 | GET | `/rooms/{room_id}` | none | 200 OK | 404 unknown room |
| 4 | GET | `/bookings` | none | 200 OK (list) | none |
| 5 | GET | `/bookings/{booking_id}` | none | 200 OK | 404 unknown booking |
| 6 | PUT | `/bookings/{booking_id}` | `{room_id, date, start_hour, end_hour, purpose}` | 201 Created (new ID) or 200 OK (identical retry) | 404 unknown room; 409 ID reused with different details, or slot overlaps another booking; 422 invalid body |
| 7 | DELETE | `/bookings/{booking_id}` | none | 204 No Content | 404 unknown booking |

The client chooses the booking ID, so bookings are created with `PUT` (there is no `POST /bookings`). Bookings are immutable: to change one, delete it and create a new one.

## Models

**Room:** `room_id` (string, e.g. `B205`), `building` (string), `capacity` (integer).

**Booking:** `booking_id` (from the URI), `room_id`, `date` (`YYYY-MM-DD`), `start_hour` and `end_hour` (integers, whole hours), `purpose` (string).

Example:

```json
{"booking_id": "bk-1", "room_id": "B205", "date": "2030-05-10",
 "start_hour": 10, "end_hour": 12, "purpose": "Lecture"}
```

## Validation rules (all violations return 422)

- Room: `room_id` and `building` not empty (blank/whitespace rejected); `capacity` an integer greater than 0.
- Booking: all five fields required; correct types; `date` a real calendar date; `start_hour >= 8`; `end_hour <= 20`; `start_hour < end_hour`; `purpose` not blank.
- 404: unknown room (GET room, or booking for a room that does not exist) and unknown booking (GET, DELETE).
- 409: duplicate room ID; booking conflicts (see below).

## Overlap prevention

Two bookings conflict when they use the same room, the same date, and their intervals overlap:

```python
new_start < existing_end and existing_start < new_end
```

Examples with an existing booking B205, 10:00-12:00: 11-13 is rejected; 10-12 under another ID is rejected; 12-13 and 8-10 are allowed (they only touch); another room, or another date, is allowed.

## Idempotent PUT

`PUT /bookings/{booking_id}` behaves like this:

| Scenario | Result |
|----------|--------|
| A. New ID | 201, booking created |
| B. Same ID, identical payload | 200, nothing new created (booking count unchanged) |
| C. Same ID, different details | 409 (immutable-booking design) |
| D. Different ID, overlapping room/date/time | 409 |

**Why the client must reuse the same ID when retrying.** If a response is lost (timeout, dropped connection), the client cannot know whether the booking was created. If it retries with a *new* ID, the server sees a second booking for the same slot: it is rejected with 409 (or, for a different slot, silently creates a duplicate). If it retries with the *same* ID and the same payload, the server recognises the request and answers 200 with the existing booking, so the retry is safe whether or not the first attempt arrived.

**Three different ideas:**

- **Idempotency**: repeating the *same* request (same ID, same payload) has the same effect as sending it once. It protects against retries.
- **Preventing overlapping reservations**: a business rule that two *different* bookings may not share a room and time. A repeated identical request is not an overlap; it is the same booking.
- **Preventing race conditions**: making sure that two requests running at the same moment cannot both pass the overlap check before either one saves. This service uses one `threading.Lock` around "check for conflicts, then insert" (and around the existing-ID check), so those steps happen as one atomic unit.

## Actual HTTPX client output

Real output of `test_bookings.py` against the running Uvicorn server (fastapi 0.165.0, pydantic 2.14.4, uvicorn 0.54.0, httpx 0.28.1). The script was also run twice more against the same server (43 of 43 passed each time).

```text
Testing http://127.0.0.1:8000 (run id 22a0d2)

[PASS] 1. Create valid room
       POST /rooms | actual 201 | expected 201
[PASS] 1b. Create second room (for other-room test)
       POST /rooms | actual 201 | expected 201
[PASS] 2. List rooms
       GET /rooms | actual 200 | expected 200
[PASS] 3. Retrieve existing room
       GET /rooms/B22a0d2 | actual 200 | expected 200
[PASS] 4. Retrieve unknown room
       GET /rooms/NOPE22a0d2 | actual 404 | expected 404
[PASS] 5. Create duplicate room
       POST /rooms | actual 409 | expected 409
[PASS] 6. Invalid room capacity (0)
       POST /rooms | actual 422 | expected 422
[PASS] 6b. Empty room ID
       POST /rooms | actual 422 | expected 422
[PASS] 6c. Empty building
       POST /rooms | actual 422 | expected 422
[PASS] 7. Create valid booking (10-12)
       PUT /bookings/bk-22a0d2-1 | actual 201 | expected 201
[PASS] 8. Retrieve existing booking
       GET /bookings/bk-22a0d2-1 | actual 200 | expected 200
[PASS] 8b. List bookings
       GET /bookings | actual 200 | expected 200
[PASS] 9. Retrieve unknown booking
       GET /bookings/nope-22a0d2 | actual 404 | expected 404
[PASS] 10. Booking for unknown room
       PUT /bookings/bk-22a0d2-unk | actual 404 | expected 404
[PASS] 11. Overlapping booking (11-13)
       PUT /bookings/bk-22a0d2-ov1 | actual 409 | expected 409
[PASS] 11b. Same slot, different ID (10-12)
       PUT /bookings/bk-22a0d2-ov2 | actual 409 | expected 409
[PASS] 11c. Fully inside existing (10-11)
       PUT /bookings/bk-22a0d2-ov3 | actual 409 | expected 409
[PASS] 12. Adjacent booking after (12-13)
       PUT /bookings/bk-22a0d2-adj1 | actual 201 | expected 201
[PASS] 12b. Adjacent booking before (08-10)
       PUT /bookings/bk-22a0d2-adj2 | actual 201 | expected 201
[PASS] 13. Same time, different room
       PUT /bookings/bk-22a0d2-room2 | actual 201 | expected 201
[PASS] 14. Same time, different date
       PUT /bookings/bk-22a0d2-date2 | actual 201 | expected 201
[PASS] 15. start_hour >= end_hour
       PUT /bookings/bk-22a0d2-v1 | actual 422 | expected 422
[PASS] 15b. start_hour after end_hour
       PUT /bookings/bk-22a0d2-v1b | actual 422 | expected 422
[PASS] 16. Start before 08:00
       PUT /bookings/bk-22a0d2-v2 | actual 422 | expected 422
[PASS] 17. End after 20:00
       PUT /bookings/bk-22a0d2-v3 | actual 422 | expected 422
[PASS] 18. Empty purpose
       PUT /bookings/bk-22a0d2-v4 | actual 422 | expected 422
[PASS] 19. Missing required field (end_hour)
       PUT /bookings/bk-22a0d2-v5 | actual 422 | expected 422
[PASS] 20. Wrong field type (start_hour = 'ten')
       PUT /bookings/bk-22a0d2-v6 | actual 422 | expected 422
[PASS] 20b. Invalid date
       PUT /bookings/bk-22a0d2-v7 | actual 422 | expected 422
[PASS] A. First PUT with new ID creates booking
       PUT /bookings/bk-22a0d2-idem | actual 201 | expected 201
[PASS] B. Retry identical PUT (same ID, same payload)
       PUT /bookings/bk-22a0d2-idem | actual 200 | expected 200
[PASS] B2. Booking count unchanged by retry
       GET /bookings | count before 6 | count after 6
[PASS] C. Same ID, different details
       PUT /bookings/bk-22a0d2-idem | actual 409 | expected 409
[PASS] D. Different ID, overlapping slot
       PUT /bookings/bk-22a0d2-idem-other | actual 409 | expected 409
[PASS] D2. Original booking still unchanged
       GET /bookings/bk-22a0d2-idem | actual 200 | expected 200
[PASS] 21. Delete existing booking
       DELETE /bookings/bk-22a0d2-idem | actual 204 | expected 204
[PASS] 21b. Deleted booking is gone
       GET /bookings/bk-22a0d2-idem | actual 404 | expected 404
[PASS] 21c. Slot is free again after delete
       PUT /bookings/bk-22a0d2-idem-reuse | actual 201 | expected 201
[PASS] 22. Delete unknown booking
       DELETE /bookings/nope-22a0d2 | actual 404 | expected 404
[PASS] 23. 10 concurrent PUTs, different IDs, same slot
       statuses [201, 409, 409, 409, 409, 409, 409, 409, 409, 409] | expected exactly one 201 and nine 409
[PASS] 23b. Only one booking stored for that slot
       GET /bookings | stored 1 | expected 1
[PASS] 24. 10 concurrent identical PUTs, same ID
       statuses [200, 200, 200, 200, 200, 200, 200, 200, 200, 201] | expected one 201 and nine 200
[PASS] 24b. Only one booking stored for that ID
       GET /bookings | stored 1 | expected 1

Total: 43 | Passed: 43 | Failed: 0
```

### Status codes demonstrated

| Code | Where it appears | Meaning |
|------|------------------|---------|
| 200 | tests 2, 3, 8, 8b, B, D2, 24 | Read succeeded; identical PUT retry returned the existing booking |
| 201 | tests 1, 7, 12, 13, 14, A, 21c, 23, 24 | Room or booking created |
| 204 | test 21 | Booking deleted, no body |
| 404 | tests 4, 9, 10, 21b, 22 | Unknown room or booking |
| 409 | tests 5, 11, 11b, 11c, C, D, 23 | Duplicate room, overlap, or booking ID reused with different details |
| 422 | tests 6, 6b, 6c, 15-20b | Pydantic validation failed (bad capacity, hours, empty text, missing field, wrong type, invalid date) |

### Evidence that a retry does not duplicate a booking

- Test A creates booking `bk-...-idem` (201). Test B repeats the identical PUT and gets 200.
- Test B2 reads `GET /bookings` before and after the retry: the count is the same (6 and 6).
- Test 24 sends 10 identical PUTs at the same time: exactly one 201 and nine 200, and test 24b confirms one stored booking.

### Concurrency result

Test 23 sends 10 simultaneous PUTs with different IDs for the same room, date and time: exactly one succeeded (201), nine returned 409, and test 23b confirms only one booking is stored. This was run against a single Uvicorn process.

## Limitations

- **In-memory storage is not persistent:** all rooms and bookings are lost when the server stops or restarts.
- **The lock is process-local:** `threading.Lock` protects only one server process. If several independent instances (or Uvicorn `--workers N`) run, each has its own memory and its own lock, so they would not share data or prevent conflicts across instances. A shared database with transactions or unique constraints would be needed for that; it is out of scope for this lab.
- Bookings are whole-hour and immutable (no update endpoint); there is no authentication.
