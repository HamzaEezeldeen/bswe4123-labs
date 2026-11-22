"""Simple health-check service (FastAPI + Uvicorn).

Usage:
    python service.py --port 8001
    python service.py --port 8002 --delay 0.8     # deliberately slow instance

The delay can also be set with the HEALTH_DELAY environment variable.
"""
import argparse
import asyncio
import os
import time

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="Lab 1 health service")

# Uptime starts when the process starts. A monotonic clock never jumps
# backwards, even if the system clock is changed.
START = time.monotonic()

# Optional artificial delay (seconds) before answering /health.
DELAY = float(os.environ.get("HEALTH_DELAY", "0"))


@app.get("/health")
async def health():
    if DELAY > 0:
        await asyncio.sleep(DELAY)  # non-blocking: other requests still work
    return {"status": "ok", "uptime_seconds": round(time.monotonic() - START, 1)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 1 health service")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--delay", type=float, default=None,
                        help="seconds to wait before answering /health (default 0)")
    args = parser.parse_args()
    if args.delay is not None:
        DELAY = args.delay
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
