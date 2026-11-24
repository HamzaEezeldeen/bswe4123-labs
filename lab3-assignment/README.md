# Lab Assignment 3: The Exam Scheduler Service

- **University:** University of Palestine
- **Course:** Distributed Systems Lab (BSWE4123)
- **Student:** Hamza Ezzeldeen Mustafa Elkhatib
- **Student ID:** 120231615

## Description

An **Exam Scheduler Service** that finds and books a room for an exam by calling the **Room Booking Service** from Lab 2 over HTTP. The scheduler reads its settings from environment variables, applies a timeout to every call, retries transient failures with exponential backoff, and uses a stable booking ID so that retries can never create a second booking for the same exam.

> The assignment PDF was not available in the workspace, so this implementation follows the written requirements supplied with the task.

## Architecture

```text
 client --POST /exams--> Exam Scheduler (8004) --HTTP, timeout, retries--> Room Booking Service (8005)
                              GET /config                                     SQLite file (bookings.db)
```

| Service | File | Port (default) | Role |
|---------|------|----------------|------|
| Exam Scheduler | `scheduler_service.py` | 8004 | Chooses a room, books it, handles retries |
| Room Booking Service | `booking_service.py` | 8005 | Owns rooms and bookings; decides whether a booking is accepted |

`booking_service.py` is the Lab 2 service with the **same API and status codes** (`GET/POST /rooms`, `GET /rooms/{id}`, `GET /bookings`, `GET /bookings/{id}`, `PUT /bookings/{id}`, `DELETE /bookings/{id}`; 201/200/404/409/422). Two changes were made in this copy only (Lab 2 is untouched):

1. Storage is **SQLite** instead of a Python dictionary (see [Why SQLite](#why-sqlite)).
2. An optional artificial **response delay** (`BOOKING_SERVICE_DELAY_SECONDS`).

## Technologies

Python 3, FastAPI, Pydantic, Uvicorn, HTTPX (async), SQLite (standard library `sqlite3`).

## Project structure

```text
lab3-assignment/
  scheduler_service.py   Exam Scheduler Service (POST /exams, GET /config, retry helper)
  booking_service.py     Room Booking Service (Lab 2 API, SQLite, optional delay)
  test_scheduler.py      Integration tests (starts the real services itself)
  run_experiments.py     Stop/restart experiment and delay/timeout experiments
  harness.py             Helpers that start/stop services as subprocesses
  stub_service.py        Tiny fake service that returns 500/404/409 (retry-policy tests only)
  experiment_output.txt  Saved real output of run_experiments.py
  requirements.txt, .gitignore, README.md
```

## Installation (Windows PowerShell)

```powershell
cd lab3-assignment
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Running the services

Room Booking Service (terminal 1):

```powershell
$env:BOOKING_DB_PATH = "bookings.db"
$env:BOOKING_SERVICE_DELAY_SECONDS = "0"
.\.venv\Scripts\python -m uvicorn booking_service:app --port 8005
```

Exam Scheduler (terminal 2):

```powershell
$env:ROOM_BOOKING_SERVICE_URL = "http://127.0.0.1:8005"
$env:REQUEST_TIMEOUT_SECONDS = "1.0"
$env:MAX_RETRIES = "3"
.\.venv\Scripts\python -m uvicorn scheduler_service:app --port 8004
```

Add a room and schedule an exam (terminal 3):

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8005/rooms -ContentType application/json -Body '{"room_id":"B205","building":"Engineering","capacity":40}'
Invoke-RestMethod -Method Post http://127.0.0.1:8004/exams -ContentType application/json -Body '{"exam_id":"CS101-final","course_code":"CS101","date":"2030-06-15","start_hour":9,"end_hour":11,"num_students":35}'
```

### Environment variables

| Variable | Service | Default | Meaning |
|----------|---------|---------|---------|
| `ROOM_BOOKING_SERVICE_URL` | scheduler | `http://127.0.0.1:8005` | Base URL of the booking service (must start with `http://` or `https://`) |
| `REQUEST_TIMEOUT_SECONDS` | scheduler | `1.0` | Timeout of **each** HTTP attempt (number > 0) |
| `MAX_RETRIES` | scheduler | `3` | **Additional** attempts after the first one (integer 0-10), so at most `MAX_RETRIES + 1` attempts per call |
| `BACKOFF_BASE_SECONDS` | scheduler | `0.5` | Wait before retry *n* is `base * 2^(n-1)`: 0.5 s, 1 s, 2 s ... |
| `BOOKING_DB_PATH` | booking | `bookings.db` | SQLite file |
| `BOOKING_SERVICE_DELAY_SECONDS` | booking | `0` | Artificial delay added to every response |
| `BOOKING_SERVICE_DELAY_METHODS` | booking | `GET,POST,PUT,DELETE` | Which HTTP methods are delayed (`/health` is never delayed) |

Invalid values stop the scheduler at startup with a clear error. No secrets are used or exposed.

### GET /config

Returns the values the scheduler is actually using:

```json
{
  "room_booking_service_url": "http://127.0.0.1:8005",
  "request_timeout_seconds": 1.0,
  "max_retries": 3,
  "backoff_base_seconds": 0.5
}
```

## POST /exams

Request:

```json
{"exam_id": "CS101-final", "course_code": "CS101", "date": "2030-06-15",
 "start_hour": 9, "end_hour": 11, "num_students": 35}
```

Response `201 Created`:

```json
{"exam_id": "CS101-final", "course_code": "CS101", "date": "2030-06-15",
 "start_hour": 9, "end_hour": 11, "num_students": 35,
 "booking_id": "exam-CS101-final", "room_id": "B205", "retries_used": 0}
```

| Status | When |
|--------|------|
| **201 Created** | Exam scheduled, room booked |
| **200 OK** | The *identical* request was already scheduled: the existing result is returned, no new booking |
| **409 Conflict** | No room is large enough, no large-enough room is free at that time, or the exam ID already exists with *different* details |
| **503 Service Unavailable** | Booking service unreachable/timing out/returning 5xx after all retries |
| **422 Unprocessable Content** | Invalid input (hours outside 08-20, start >= end, 0 students, empty course code, bad date, missing field, wrong type) |
| 502 Bad Gateway | The booking service answered with an unexpected status (not seen in normal use) |

`retries_used` is the number of retries this request needed.

## Room selection algorithm

1. Validate the request (422 on error).
2. Look for an existing booking with the stable ID `exam-<exam_id>`. If it describes this exact exam, return it (200); if the ID exists with different details, return 409.
3. `GET /rooms`, drop rooms with `capacity < num_students`, sort by `(capacity, room_id)` (smallest first, room ID breaks ties). If none is left: 409.
4. For each eligible room in order, `PUT /bookings/exam-<exam_id>` with that room.
   - 201/200: done.
   - 409: either the room is taken at that time, or a concurrent identical request already booked this exam. The scheduler reads `GET /bookings/exam-<exam_id>` to tell these apart: if the exam's booking exists, return it; otherwise try the next room.
   - 404 (room vanished): try the next room.
5. If every eligible room was rejected: 409.

The scheduler never assumes a room stays free between looking and booking. The **booking service is the authority**: it checks for overlaps and inserts in one atomic step, and the scheduler simply reacts to its answer.

## Retry policy and exponential backoff

Every call to the booking service (`GET /bookings/{id}`, `GET /rooms`, `PUT /bookings/{id}`) goes through one helper, `call_booking_service`, using async HTTPX and `asyncio.sleep` (the event loop is never blocked).

| Result | Retried? |
|--------|----------|
| Timeout (`ReadTimeout`, `ConnectTimeout`) | Yes |
| Connection error | Yes |
| HTTP 5xx | Yes |
| HTTP 404, 409, 422 and other 4xx | **No**: these are real answers, not temporary failures |

`MAX_RETRIES` counts attempts *after* the first. With `MAX_RETRIES=3` there are 4 attempts and 3 waits of 0.5 s, 1.0 s and 2.0 s. Each attempt and wait is logged (operation, attempt number, failure type, backoff, whether it will retry, final result).

**Why exponential backoff:** if a service is struggling, retrying immediately and constantly adds load exactly when it can least handle it. Doubling the wait gives it time to recover, while the first retry is still quick for short glitches.

## Retry safety: stable booking IDs

A timeout does not mean the booking failed: the service may have saved it and only the *reply* was slow or lost. Therefore:

- The booking ID is always `exam-<exam_id>`, never random. Every retry and every repeated request uses the same ID, so the booking service's idempotent `PUT` (Lab 2) answers 200 instead of creating a second booking.
- If the `PUT` of a room ends in an **unknown** result (all attempts timed out), the scheduler does **not** try another room (that could book two rooms). It answers **503** and says the booking may or may not exist. Repeating the same request first looks up `exam-<exam_id>` and returns the booking if it was created.
- Same exam ID with different details gives 409; the original booking is never modified or duplicated.

### Why SQLite

Lab 2 keeps bookings in memory. If the booking service crashes or restarts after saving a booking but before the scheduler hears about it, the booking would vanish, and a restart-based retry demonstration would be misleading (a retry would book again as if nothing had happened). SQLite makes bookings **durable**. `PUT` runs inside `BEGIN IMMEDIATE`, so the conflict check and the insert are one atomic step, and the primary key on `booking_id` guarantees one row per ID.

**What is guaranteed:** one booking per exam ID (idempotent booking effect) for a single SQLite database file, including across restarts of the booking service.
**What is not claimed:** exactly-once execution in a general distributed system, high availability, or replication. If two different exams race for the same room, the first to commit wins and the other is rejected with 409 (and moves on to another room).

## Actual stop/restart (failure and recovery) experiment

Real processes. The booking service was stopped, `POST /exams` was sent while it was down, and the service was restarted while the scheduler was still retrying (`MAX_RETRIES=5`, timeout 1.0 s, backoff base 0.5 s). Output of `python run_experiments.py` (the second part of this output is the delay table, discussed further below):

```text
=== Experiment 1: stop and restart the Room Booking Service ===
[t=+ 0.0s] Room Booking Service STOPPED
[t=+ 0.0s] POST /exams sent (booking service is down)
[t=+ 3.0s] Room Booking Service restart requested
[t=+ 4.8s] Room Booking Service is up again (answering /health)
[t=+ 6.7s] POST /exams answered HTTP 201

Scheduler log (retry lines):
10:20:00.173 GET /bookings/exam-recover1 attempt 1/6 failed: ConnectTimeout | backoff 0.50s, will retry
10:20:01.693 GET /bookings/exam-recover1 attempt 2/6 failed: ConnectTimeout | backoff 1.00s, will retry
10:20:03.723 GET /bookings/exam-recover1 attempt 3/6 failed: ConnectTimeout | backoff 2.00s, will retry
10:20:05.742 GET /bookings/exam-recover1 attempt 4/6 -> HTTP 404
10:20:05.751 GET /rooms attempt 1/6 -> HTTP 200
10:20:05.774 PUT /bookings/exam-recover1 attempt 1/6 -> HTTP 201
10:20:05.774 exam recover1: booked room R40a (HTTP 201)
10:20:05.866 GET /bookings/exam-recover1 attempt 1/6 -> HTTP 200
10:20:05.867 exam recover1: already scheduled in R40a, returning it

POST /exams -> HTTP 201 after 6.65 s, retries_used=3, room=R40a
Bookings stored for exam 'recover1': 1
Identical repeat -> HTTP 200; bookings stored now: 1

=== Experiment 2: delay / timeout combinations (MAX_RETRIES=3, backoff base 0.5 s) ===
| Exp | Delay (s) | Timeout (s) | HTTP Status | Response Time (ms) | Retries | Observation |
|-----|-----------|-------------|-------------|--------------------|---------|-------------|
| A | 0.1 | 1.0 | 201 | 420 | 0 | Booked in R40a; no retries needed |
| B | 0.8 | 1.5 | 201 | 2567 | 0 | Booked in R40a; no retries needed |
| C | 1.5 | 0.5 | 503 | 5645 | 3 | Gave up after 3 retries; booking was not created |
| D | 1.5 (PUT only) | 0.5 | 503 | 5699 | 3 | Gave up after 3 retries; booking EXISTS on the server (reply was too slow) |

Follow-up for D: same request, service no longer slow -> HTTP 200, bookings stored for that exam: 1

Scheduler log of experiment C (timeouts and backoff):
10:20:33.248 GET /bookings/exam-exp-C attempt 1/4 failed: ReadTimeout | backoff 0.50s, will retry
10:20:34.277 GET /bookings/exam-exp-C attempt 2/4 failed: ReadTimeout | backoff 1.00s, will retry
10:20:35.807 GET /bookings/exam-exp-C attempt 3/4 failed: ReadTimeout | backoff 2.00s, will retry
10:20:38.336 GET /bookings/exam-exp-C attempt 4/4 failed: ReadTimeout | no attempts left, giving up
10:20:38.336 exam exp-C: FAILED, GET /bookings/exam-exp-C failed after 4 attempts (ReadTimeout)
```

What happened: the first three attempts failed with `ConnectTimeout` (nothing was listening; on Windows a refused local connection is reported here as a connect timeout), with waits of 0.5 s, 1.0 s and 2.0 s. The service came back during the third wait, the fourth attempt succeeded, and the request completed with **201** and `retries_used=3`. Exactly one booking existed, and an identical repeat returned **200** with still one booking. If the service had stayed down, the scheduler would have returned 503 (shown in the tests below, test group C).

## Delay and timeout experiments

The booking service holds each response for `BOOKING_SERVICE_DELAY_SECONDS` *after* doing the work. This simulates a slow network or an overloaded service: the booking is already saved while the caller is still waiting. Each experiment restarts both services with new environment variables, sends a real `POST /exams`, and measures end-to-end time with `time.monotonic()` (`MAX_RETRIES=3`, backoff base 0.5 s).

| Exp | Delay (s) | Timeout (s) | HTTP Status | Response Time (ms) | Retries | Observation |
| A | 0.1 | 1.0 | 201 | 420 | 0 | Booked in R40a; no retries needed |
| B | 0.8 | 1.5 | 201 | 2567 | 0 | Booked in R40a; no retries needed |
| C | 1.5 | 0.5 | 503 | 5645 | 3 | Gave up after 3 retries; booking was not created |
| D | 1.5 (PUT only) | 0.5 | 503 | 5699 | 3 | Gave up after 3 retries; booking EXISTS on the server (reply was too slow) |

A is the suggested "fast" case, B "slower but successful", C "timeouts and retries". Experiment D is an extra case where only `PUT` is slow, to show the ambiguous-timeout situation.

The response time includes three booking-service calls (look up the exam, list rooms, book), so it is roughly 3 x delay plus work. These are single measurements on one machine; exact milliseconds vary between runs.

**Observations**

- **A (0.1 s delay, 1.0 s timeout):** every call finished well inside the timeout, no retries, 201.
- **B (0.8 s delay, 1.5 s timeout):** still within the timeout, so no retries and 201, but about 2.5 s end to end. A longer timeout tolerates a slow service at the cost of a longer wait for the user.
- **C (1.5 s delay, 0.5 s timeout):** every attempt timed out. The first call was retried 3 times (waits 0.5, 1.0, 2.0 s) and the scheduler gave up with **503** after about 5.6 s. Only `GET` calls ran, so no booking was created. A timeout shorter than the real response time makes a working (just slow) service look dead and wastes effort on retries.
- **D (only `PUT` delayed 1.5 s, 0.5 s timeout):** the lookups were fast, but the booking call timed out on all 4 attempts, so the answer was **503**. **Yet the booking existed on the server**, because it was saved before the reply was delayed. This is why a timeout is *not proof of failure*. Repeating the same request afterwards (service no longer slow) returned **200** with exactly one booking; no duplicate was created.

**Trade-offs**

- *Short timeouts* detect a dead service quickly but declare slow services failed and trigger unnecessary retries (C, D).
- *Long timeouts* tolerate slow services but make users wait longer, and a really dead service takes longer to detect (B).
- *Exponential backoff* spreads retries out so a recovering service is not hammered; the price is a longer total wait (about 5.6 s in C).
- *Idempotency* is what makes retries safe: without a stable booking ID, the retries in D would have created duplicate bookings.

## Actual integration-test output

`python test_scheduler.py` starts the real services itself (ports 8004-8017, a temporary database), runs every test, and exits with code 0 only if all pass. It covers scheduling, validation, configuration, retry policy, idempotency (including concurrent and lost/late replies), the restart recovery, and all delay experiments.

```text
--- Setup: two services on separate ports ---
[PASS] Booking service answers on port 8005
       actual: 200 | expected: 200
[PASS] Scheduler answers on a different port (8004)
       actual: 200 | expected: 200

--- A. Scheduling (rooms: R20=20, R40a=40, R40b=40, R100=100 seats) ---
[PASS] Valid exam returns 201
       actual: 201 | expected: 201
[PASS] Smallest suitable room chosen (30 students -> R40a, tie broken by room ID)
       actual: 'R40a' | expected: 'R40a'
[PASS] Stable booking ID is exam-s1
       actual: 'exam-s1' | expected: 'exam-s1'
[PASS] Booking really exists in the booking service
       actual: 'R40a' | expected: 'R40a'
[PASS] Occupied room R40a is skipped -> R40b
       actual: (201, 'R40b') | expected: (201, 'R40b')
[PASS] Larger free room chosen when smaller ones are taken -> R100
       actual: (201, 'R100') | expected: (201, 'R100')
[PASS] No free eligible room -> 409
       actual: 409 | expected: 409
[PASS] No booking was created for the failed exam
       actual: 3 | expected: 3
[PASS] No sufficiently large room -> 409
       actual: 409 | expected: 409
[PASS] ... with a helpful message
       actual: True | expected: True
[PASS] 15 students -> smallest room R20
       actual: (201, 'R20') | expected: (201, 'R20')
[PASS] Partly overlapping time -> every eligible room clashes -> 409
       actual: 409 | expected: 409
[PASS] Adjacent time slot (12-14) is allowed -> R40a
       actual: (201, 'R40a') | expected: (201, 'R40a')
[PASS] Invalid exam (start hour before 08:00) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (end hour after 20:00) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (start >= end) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (zero students) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (empty course code) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (empty exam id) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (invalid date) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (wrong type (num_students='many')) -> 422
       actual: 422 | expected: 422
[PASS] Invalid exam (missing field (date)) -> 422
       actual: 422 | expected: 422

--- B. Configuration ---
[PASS] GET /config shows the active settings
       actual: {'room_booking_service_url': 'http://127.0.0.1:8005', 'request_timeout_seconds': 1.0, 'max_retries': 2, 'backoff_base_seconds': 0.1} | expected: {'room_booking_service_url': 'http://127.0.0.1:8005', 'request_timeout_seconds': 1.0, 'max_retries': 2, 'backoff_base_seconds': 0.1}
[PASS] Invalid setting {'REQUEST_TIMEOUT_SECONDS': '0'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Invalid setting {'REQUEST_TIMEOUT_SECONDS': 'fast'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Invalid setting {'MAX_RETRIES': '-1'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Invalid setting {'MAX_RETRIES': '2.5'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Invalid setting {'ROOM_BOOKING_SERVICE_URL': 'localhost'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Invalid setting {'BACKOFF_BASE_SECONDS': '-1'} is rejected
       actual: 'rejected' | expected: 'rejected'
[PASS] Defaults are applied when nothing is set
       actual: Settings(room_booking_service_url='http://127.0.0.1:8005', request_timeout_seconds=1.0, max_retries=3, backoff_base_seconds=0.5) | expected: Settings(room_booking_service_url='http://127.0.0.1:8005', request_timeout_seconds=1.0, max_retries=3, backoff_base_seconds=0.5)

--- C. Retry policy ---
[PASS] Backoff intervals for base 0.5 s
       actual: [0.5, 1.0, 2.0] | expected: [0.5, 1.0, 2.0]
[PASS] Connection failure: retried, then 503
       actual: (503, 2) | expected: (503, 2)
[PASS] ... 3 attempts logged with exponential backoff
       actual: True | expected: True
[PASS] ... total time includes the backoff waits (>= 0.3 s)
       actual: True | expected: True
[PASS] Timeout: retried, then 503
       actual: (503, 1) | expected: (503, 1)
[PASS] ... log shows a timeout failure
       actual: True | expected: True
[PASS] HTTP 500: retried, then 503
       actual: (503, 2) | expected: (503, 2)
[PASS] ... stub received 3 identical requests (1 + 2 retries)
       actual: 3 | expected: 3
[PASS] ... backoff doubles in the log
       actual: True | expected: True
[PASS] HTTP 404 is not retried (PUT sent once, retries_used 0)
       actual: (1, 0) | expected: (1, 0)
[PASS] ... room skipped, no room left -> 409
       actual: 409 | expected: 409
[PASS] HTTP 409 is not retried (PUT sent once, retries_used 0)
       actual: (1, 0) | expected: (1, 0)
[PASS] ... no free room -> 409 (not 503)
       actual: 409 | expected: 409

--- D. Idempotency ---
[PASS] Identical repeat of exam s1 returns 200 with the same booking
       actual: (200, 'R40a', 'exam-s1') | expected: (200, 'R40a', 'exam-s1')
[PASS] ... no second booking created
       actual: (1, 5) | expected: (1, 5)
[PASS] Same exam ID with different details -> 409
       actual: 409 | expected: 409
[PASS] Same exam ID on a different date -> 409
       actual: 409 | expected: 409
[PASS] ... original booking untouched, still one booking
       actual: (1, '2031-01-10') | expected: (1, '2031-01-10')
[PASS] 10 concurrent identical requests: one 201, nine 200
       actual: [200, 200, 200, 200, 200, 200, 200, 200, 200, 201] | expected: [200, 200, 200, 200, 200, 200, 200, 200, 200, 201]
[PASS] ... all agree on the room
       actual: 1 | expected: 1
[PASS] ... exactly one booking stored
       actual: 1 | expected: 1
[PASS] Reply too slow: scheduler answers 503 (result unknown)
       actual: 503 | expected: 503
[PASS] ... it retried the same booking ID (2 PUT attempts) and never tried another room
       actual: (2, False) | expected: (2, False)
[PASS] Booking was committed on the server despite the 503
       actual: (200, 1) | expected: (200, 1)
[PASS] Booking survives a service restart
       actual: 200 | expected: 200
[PASS] Repeating the same request finds the booking: 200, no duplicate
       actual: (200, 1) | expected: (200, 1)

--- E. Integration: outage recovery and delay experiments (real processes) ---
[PASS] Request sent during an outage succeeds after the restart (201)
       actual: 201 | expected: 201
[PASS] ... retries were used
       actual: True | expected: True
[PASS] ... log shows failed attempts then success
       actual: True | expected: True
[PASS] ... exactly one booking exists
       actual: 1 | expected: 1
[PASS] Identical repeat after recovery: 200, still one booking
       actual: (200, 1) | expected: (200, 1)
[PASS] Experiment A (delay 0.1, timeout 1.0): 201 without retries
       actual: (201, 0) | expected: (201, 0)
[PASS] Experiment B (delay 0.8, timeout 1.5): 201 without retries, slower than A
       actual: (201, 0, True) | expected: (201, 0, True)
[PASS] Experiment C (delay 1.5, timeout 0.5): retries exhausted -> 503
       actual: (503, 3) | expected: (503, 3)
[PASS] Experiment D (only PUT slow): 503 although the booking exists
       actual: (503, True) | expected: (503, True)
[PASS] ... repeating it later returns 200, still one booking
       actual: (200, 1) | expected: (200, 1)

Total tests: 68 | Passed: 68 | Failed: 0
```

## Assumptions and limitations

- The retry/idempotency guarantee is an **idempotent booking effect** for one SQLite database file; it is not general exactly-once distributed execution.
- SQLite's write lock is per database file. Several booking-service processes sharing the file would still be safe for conflict checks, but multiple servers with separate databases would not share bookings. A real deployment would use a shared database server.
- The scheduler keeps no state of its own (each request re-checks the booking service), so scheduler instances can be added freely; the booking service remains the single authority.
- If the result of a booking `PUT` stays unknown after all retries, the answer is 503 and a later identical request resolves it. The scheduler does not try other rooms in that situation, to avoid double-booking.
- The delay simulation holds the response after the work is done; it is not a model of real network loss.
- On Windows a refused local connection surfaces as `ConnectTimeout` after the connect timeout, which is why outage attempts take about as long as the timeout.
- Whole-hour bookings between 08:00 and 20:00 only; no authentication; bookings are immutable (delete and recreate to change).

## Reproducing the results

```powershell
cd lab3-assignment
.\.venv\Scripts\python run_experiments.py   # stop/restart + delay experiments, writes experiment_output.txt
.\.venv\Scripts\python test_scheduler.py    # all integration tests
```

Both scripts need ports 8004-8017 free and take about one to three minutes.
