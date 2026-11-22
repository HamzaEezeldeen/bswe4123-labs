# Lab Assignment 1: Is the Service Up, Slow or Down?

- **University:** University of Palestine
- **Course:** Distributed Systems Lab (BSWE4123)
- **Student:** Hamza Ezzeldeen Mustafa Elkhatib
- **Student ID:** 120231615

## Project description

A tiny health-check system. `service.py` is an HTTP service with a `/health` endpoint. Three independent copies run on ports 8001, 8002 and 8003. `monitor.py` checks all three every 2 seconds and prints one line per round that says whether each service is **UP**, **SLOW**, **SUSPECT** or **DOWN**.

> Note: the code from the previous Lab 1 was not available when this lab was written, so `service.py` was written from scratch using FastAPI/Uvicorn.

## Technologies

- Python 3
- FastAPI + Uvicorn (the service)
- HTTPX + asyncio (the monitor, concurrent requests)

## Installation

```powershell
pip install -r requirements.txt
```

## Starting the services

Open three terminals (or use `Start-Process`), one per port:

```powershell
python service.py --port 8001
python service.py --port 8002
python service.py --port 8003
```

A deliberately slow instance (0.8 s delay before answering `/health`) is started with `--delay`; the other instances are unaffected:

```powershell
python service.py --port 8002 --delay 0.8
```

(The `HEALTH_DELAY` environment variable does the same thing.)

## Running the monitor

```powershell
python monitor.py                 # runs until Ctrl+C
python monitor.py --duration 60   # stops after 60 seconds
```

## Health endpoint

`GET /health` returns HTTP 200 and JSON. `uptime_seconds` is measured with `time.monotonic()` starting when the process starts.

```json
{
  "status": "ok",
  "uptime_seconds": 15.4
}
```

## Status meanings

| Status | Meaning |
|--------|---------|
| **UP** | Valid response (HTTP 200, `"status": "ok"`) in 500 ms or less. |
| **SLOW** | Valid response, but it took more than 500 ms. This is *not* a failure. |
| **SUSPECT (n/3 failures)** | Intermediate state: the last `n` checks in a row failed (n = 1 or 2). Not yet declared down. |
| **DOWN (n/3 failures)** | 3 or more consecutive failures. The counter keeps growing (4/3, 5/3, ...). |
| **RECOVERED** | Printed next to UP/SLOW on the first valid response after DOWN. |

A failure is: connection refused/failed, timeout, non-200 status, malformed JSON, or a body whose `status` is not `ok`. Each service has its own failure counter, and each check updates it exactly once. Any valid response (UP or SLOW) resets the counter to 0.

## Actual output of the 60-second experiment

This is the real, unedited output of `python run_experiment.py` (also saved in `experiment_output.txt`). All three services were real processes on ports 8001-8003.

```text
[00:00] 8001: UP (148ms) | 8002: UP (22ms) | 8003: UP (22ms)
[00:02] 8001: UP (12ms) | 8002: UP (10ms) | 8003: UP (9ms)
[00:04] 8001: UP (20ms) | 8002: UP (21ms) | 8003: UP (18ms)
[00:06] 8001: UP (22ms) | 8002: UP (22ms) | 8003: UP (15ms)
[00:08] 8001: UP (27ms) | 8002: UP (19ms) | 8003: SUSPECT (1/3 failures, connect timeout)
[00:10] 8001: UP (21ms) | 8002: UP (17ms) | 8003: SUSPECT (2/3 failures, connect timeout)
[00:12] 8001: UP (15ms) | 8002: UP (15ms) | 8003: DOWN (3/3 failures, connect timeout)
[00:14] 8001: UP (16ms) | 8002: UP (14ms) | 8003: DOWN (4/3 failures, connect timeout)
[00:16] 8001: UP (32ms) | 8002: UP (28ms) | 8003: DOWN (5/3 failures, connect timeout)
[00:18] 8001: UP (11ms) | 8002: UP (10ms) | 8003: DOWN (6/3 failures, connect timeout)
[00:20] 8001: UP (24ms) | 8002: UP (18ms) | 8003: DOWN (7/3 failures, connect timeout)
[00:22] 8001: UP (14ms) | 8002: UP (13ms) | 8003: UP (22ms) RECOVERED
[00:24] 8001: UP (12ms) | 8002: UP (14ms) | 8003: UP (13ms)
[00:26] 8001: UP (24ms) | 8002: UP (24ms) | 8003: UP (21ms)
[00:28] 8001: UP (20ms) | 8002: UP (21ms) | 8003: UP (20ms)
[00:30] 8001: UP (24ms) | 8002: SUSPECT (1/3 failures, connect timeout) | 8003: UP (18ms)
[00:32] 8001: UP (23ms) | 8002: SLOW (834ms) | 8003: UP (22ms)
[00:34] 8001: UP (19ms) | 8002: SLOW (827ms) | 8003: UP (15ms)
[00:36] 8001: UP (12ms) | 8002: SLOW (820ms) | 8003: UP (9ms)
[00:38] 8001: UP (13ms) | 8002: SLOW (819ms) | 8003: UP (12ms)
[00:40] 8001: UP (18ms) | 8002: SLOW (824ms) | 8003: UP (14ms)
[00:42] 8001: UP (20ms) | 8002: SLOW (815ms) | 8003: UP (14ms)
[00:44] 8001: UP (31ms) | 8002: SUSPECT (1/3 failures, connect timeout) | 8003: UP (16ms)
[00:46] 8001: UP (17ms) | 8002: UP (22ms) | 8003: UP (14ms)
[00:48] 8001: UP (15ms) | 8002: UP (15ms) | 8003: UP (18ms)
[00:50] 8001: UP (16ms) | 8002: UP (21ms) | 8003: UP (18ms)
[00:52] 8001: UP (18ms) | 8002: UP (18ms) | 8003: UP (16ms)
[00:54] 8001: UP (28ms) | 8002: UP (28ms) | 8003: UP (26ms)
[00:56] 8001: UP (17ms) | 8002: UP (16ms) | 8003: UP (14ms)
[00:58] 8001: UP (23ms) | 8002: UP (25ms) | 8003: UP (22ms)
```

### What happened

| Time | Event | Observed |
|------|-------|----------|
| 00:00-00:06 | All services normal | All UP, around 10-30 ms (the first request is slower: new connection). |
| ~00:07 | Process on 8003 stopped | 00:08 SUSPECT 1/3, 00:10 SUSPECT 2/3. |
| 00:12 | Third consecutive failure | **8003 turns DOWN.** It stays DOWN (4/3 ... 7/3) while the process is gone; 8001 and 8002 are not affected. |
| ~00:21 | 8003 restarted | 00:22 `UP (22ms) RECOVERED`: the first success after DOWN shows recovery immediately. |
| ~00:29 | 8002 restarted with 0.8 s delay | 00:30 one SUSPECT 1/3 (the process was still starting). From 00:32 it is **SLOW (~820-834 ms), never DOWN**, because 800 ms < 1.2 s timeout. |
| ~00:43 | 8002 restarted normally | 00:44 one SUSPECT 1/3 (restart gap), then UP again at 00:46. The single failure was reset by the success, so it never reached DOWN. |
| 00:46-00:58 | All normal | All UP. |

Two SUSPECT lines for 8002 at 00:30 and 00:44 are caused by the experiment script restarting that process (a real, brief outage). They are a good demonstration that one missed check does not cause a false DOWN alarm.

## Timeout and threshold design

| Setting | Value |
|---------|-------|
| Interval | 2 s |
| SLOW threshold | 500 ms |
| HTTP timeout | 1.2 s total (0.5 s connect) |
| DOWN threshold | 3 consecutive failures |

**Why these values**

- **Timeout 1.2 s:** larger than the 0.8 s test delay, so a slow-but-working service is classified SLOW and not as a failure; smaller than the 2 s interval, so a round always finishes before the next one starts (no overlapping rounds).
- **SLOW at 500 ms:** a healthy local `/health` answers in tens of milliseconds, so 500 ms is clearly abnormal while still far below the timeout.
- **3 failures:** one lost check (restart, packet loss, short pause) should not raise an alarm; three in a row is strong evidence.
- **Detection time:** about 3 x 2 s = 6 s (as seen: stopped at ~00:07, DOWN at 00:12).
- **Concurrency:** the three requests of a round run together with `asyncio.gather`, so a round takes as long as the slowest service, not the sum. Rounds are scheduled from a fixed start time, so they do not drift.

**Trade-offs**

| Choice | Advantage | Disadvantage |
|--------|-----------|--------------|
| Short timeout | Fast detection of hangs | Slow-but-healthy services are misreported as failed (false alarms). |
| Long timeout | Fewer false alarms | Failures are noticed later; a round can take longer than the interval. |
| Low threshold (1) | Fastest detection | Many false alarms from one-off glitches. |
| High threshold (e.g. 10) | Very few false alarms | Real outages are reported late (20 s here). |
| Short interval | Faster detection | More network and CPU overhead on the services. |
| Long interval | Low overhead | Delayed failure detection. |

The chosen values (2 s / 1.2 s / 3) are a middle ground: roughly 6 s to detect a real outage, with protection against single-check glitches. A drawback is that a service that is intermittently failing (for example every other check) never reaches DOWN because the counter keeps resetting.

## Reproducing the experiment

Automatic (recommended). This starts the three services, runs the monitor for 60 s, stops/restarts/slows services at fixed times, then stops everything:

```powershell
pip install -r requirements.txt
python run_experiment.py
```

Manual (use 4 terminals; timings approximate):

```powershell
# terminals 1-3
python service.py --port 8001
python service.py --port 8002
python service.py --port 8003
# terminal 4
python monitor.py --duration 60
```

Then, while the monitor runs:

1. About 8 s: press Ctrl+C in the 8003 terminal (process failure). Watch SUSPECT 1/3, 2/3, then DOWN.
2. About 20 s: start `python service.py --port 8003` again. The next line shows `RECOVERED`.
3. About 30 s: stop 8002 and start `python service.py --port 8002 --delay 0.8`. It shows SLOW, not DOWN.
4. About 44 s: stop 8002 and start `python service.py --port 8002` again.

## Files

| File | Purpose |
|------|---------|
| `service.py` | Health service (FastAPI/Uvicorn) |
| `monitor.py` | Concurrent health monitor (HTTPX) |
| `requirements.txt` | Dependencies |
| `run_experiment.py` | Automates the 60-second experiment |
| `experiment_output.txt` | Saved real output of the experiment |
