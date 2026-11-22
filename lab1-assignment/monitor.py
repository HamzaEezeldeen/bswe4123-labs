"""Health monitor: checks three services every 2 seconds.

Usage:
    python monitor.py                 # run until Ctrl+C
    python monitor.py --duration 60   # stop after about 60 seconds
"""
import argparse
import asyncio
import time

import httpx

PORTS = [8001, 8002, 8003]
INTERVAL = 2.0          # seconds between rounds
SLOW_THRESHOLD = 0.5    # > 500 ms  -> SLOW
TIMEOUT = httpx.Timeout(1.2, connect=0.5)  # total 1.2 s, connect 0.5 s
DOWN_AFTER = 3          # consecutive failures before DOWN

failures = {port: 0 for port in PORTS}  # consecutive failures per service


async def probe(client, port):
    """Return (ok, seconds, reason). A reply of any speed is ok if it is valid."""
    start = time.perf_counter()
    try:
        r = await client.get(f"http://127.0.0.1:{port}/health")
        elapsed = time.perf_counter() - start
        if r.status_code != 200:
            return False, elapsed, f"HTTP {r.status_code}"
        body = r.json()
        if not isinstance(body, dict) or body.get("status") != "ok":
            return False, elapsed, "unhealthy body"
        return True, elapsed, ""
    except httpx.ConnectTimeout:
        return False, time.perf_counter() - start, "connect timeout"
    except httpx.TimeoutException:
        return False, time.perf_counter() - start, "read timeout"
    except httpx.TransportError:
        return False, time.perf_counter() - start, "connection failed"
    except ValueError:  # r.json() on malformed body
        return False, time.perf_counter() - start, "malformed JSON"


def describe(port, ok, elapsed, reason):
    """Update this service's failure counter once and return its label."""
    ms = round(elapsed * 1000)
    if ok:
        was_down = failures[port] >= DOWN_AFTER
        failures[port] = 0  # any valid response resets the counter
        state = "SLOW" if elapsed > SLOW_THRESHOLD else "UP"
        return f"{port}: {state} ({ms}ms)" + (" RECOVERED" if was_down else "")
    failures[port] += 1
    n = failures[port]
    if n >= DOWN_AFTER:
        return f"{port}: DOWN ({n}/{DOWN_AFTER} failures, {reason})"
    return f"{port}: SUSPECT ({n}/{DOWN_AFTER} failures, {reason})"


async def main(duration):
    begin = time.monotonic()
    round_no = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while True:
            # All three requests run concurrently: a round takes as long as
            # the slowest service, not the sum of all three.
            results = await asyncio.gather(*(probe(client, p) for p in PORTS))
            elapsed = int(round_no * INTERVAL)  # label = scheduled time of this round
            parts = [describe(p, *res) for p, res in zip(PORTS, results)]
            print(f"[{elapsed // 60:02d}:{elapsed % 60:02d}] " + " | ".join(parts), flush=True)

            round_no += 1
            if duration and round_no * INTERVAL >= duration:
                break
            await asyncio.sleep(max(0, begin + round_no * INTERVAL - time.monotonic()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 1 health monitor")
    parser.add_argument("--duration", type=float, default=0,
                        help="stop after this many seconds (default: run forever)")
    try:
        asyncio.run(main(parser.parse_args().duration))
    except KeyboardInterrupt:
        pass
