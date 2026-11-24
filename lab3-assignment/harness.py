"""Helpers that start/stop the real services as subprocesses (used by tests and experiments)."""
import os
import subprocess
import sys
import tempfile
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = tempfile.mkdtemp(prefix="lab3-logs-")   # outside the repository
BOOKING_PORT, SCHEDULER_PORT = 8005, 8004
BOOKING_URL = f"http://127.0.0.1:{BOOKING_PORT}"
SCHEDULER_URL = f"http://127.0.0.1:{SCHEDULER_PORT}"

ROOMS = [("R20", "Building A", 20), ("R40a", "Building B", 40),
         ("R40b", "Building B", 40), ("R100", "Main Hall", 100)]

_started = []


class Service:
    """One uvicorn process. Its output goes to a log file we can read back."""

    def __init__(self, module, port, env=None, ready_path="/health"):
        self.module, self.port, self.ready_path = module, port, ready_path
        self.env = {k: str(v) for k, v in (env or {}).items()}
        self.proc = None
        self.log_path = os.path.join(LOG_DIR, f"{module}-{port}-{len(_started)}.log")

    def start(self):
        env = {**os.environ, "PYTHONUNBUFFERED": "1", **self.env}
        with open(self.log_path, "w") as log_file:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"{self.module}:app", "--port",
                 str(self.port), "--log-level", "warning"],
                cwd=HERE, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        _started.append(self)
        self.wait_ready()
        return self

    def wait_ready(self, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.module} exited early:\n{self.log_text()}")
            try:
                httpx.get(f"http://127.0.0.1:{self.port}{self.ready_path}", timeout=1)
                return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(f"{self.module} on port {self.port} did not start")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait()

    def log_text(self):
        with open(self.log_path) as f:
            return f.read()


def stop_all():
    for service in _started:
        service.stop()


def booking_service(db_path, delay=0, delay_methods="GET,POST,PUT,DELETE", port=BOOKING_PORT):
    return Service("booking_service", port, {
        "BOOKING_DB_PATH": db_path, "BOOKING_SERVICE_DELAY_SECONDS": delay,
        "BOOKING_SERVICE_DELAY_METHODS": delay_methods})


def scheduler_service(timeout=1.0, retries=3, base=0.5, booking_url=BOOKING_URL,
                      port=SCHEDULER_PORT):
    return Service("scheduler_service", port, {
        "ROOM_BOOKING_SERVICE_URL": booking_url, "REQUEST_TIMEOUT_SECONDS": timeout,
        "MAX_RETRIES": retries, "BACKOFF_BASE_SECONDS": base}, ready_path="/config")


def seed_rooms(booking_url=BOOKING_URL):
    for room_id, building, capacity in ROOMS:
        r = httpx.post(f"{booking_url}/rooms", timeout=10, json={
            "room_id": room_id, "building": building, "capacity": capacity})
        assert r.status_code in (201, 409), r.text


def exam(exam_id, date, start=10, end=12, students=30, course="CS101"):
    return {"exam_id": exam_id, "course_code": course, "date": date,
            "start_hour": start, "end_hour": end, "num_students": students}


def post_exam(body, scheduler_url=SCHEDULER_URL, timeout=60):
    return httpx.post(f"{scheduler_url}/exams", json=body, timeout=timeout)


def bookings_for(exam_id, booking_url=BOOKING_URL):
    """All bookings whose purpose mentions this exam (finds duplicates under any ID)."""
    rows = httpx.get(f"{booking_url}/bookings", timeout=10).json()
    return [b for b in rows if f"Exam {exam_id} " in b["purpose"]]
