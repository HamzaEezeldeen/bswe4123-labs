"""Exam Scheduler Service (FastAPI).

Books the smallest suitable free room for an exam by calling the Room Booking
Service over HTTP, with timeouts, retries and exponential backoff.

Environment variables (see README):
  ROOM_BOOKING_SERVICE_URL   default http://127.0.0.1:8005
  REQUEST_TIMEOUT_SECONDS    default 1.0   (per HTTP attempt)
  MAX_RETRIES                default 3     (extra attempts AFTER the first one)
  BACKOFF_BASE_SECONDS       default 0.5   (wait before retry n = base * 2**(n-1))

Run:  python -m uvicorn scheduler_service:app --port 8004
"""
import asyncio
import logging
import math
import os
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date as Date

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scheduler")
log.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # we log our own, clearer lines

OPEN_HOUR, CLOSE_HOUR = 8, 20   # same booking hours as Lab 2


# ---------- Configuration ----------
@dataclass(frozen=True)
class Settings:
    room_booking_service_url: str
    request_timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float


def load_settings(env=None) -> Settings:
    """Read and validate settings from environment variables."""
    env = os.environ if env is None else env

    url = env.get("ROOM_BOOKING_SERVICE_URL", "http://127.0.0.1:8005").rstrip("/")
    if not re.match(r"^https?://[^/\s]+", url):
        raise ValueError("ROOM_BOOKING_SERVICE_URL must start with http:// or https://")

    def number(name, default):
        try:
            value = float(env.get(name, default))
        except ValueError:
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        return value

    timeout = number("REQUEST_TIMEOUT_SECONDS", "1.0")
    if timeout <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than 0")

    try:
        retries = int(env.get("MAX_RETRIES", "3"))
    except ValueError:
        raise ValueError("MAX_RETRIES must be an integer")
    if not 0 <= retries <= 10:
        raise ValueError("MAX_RETRIES must be between 0 and 10")

    base = number("BACKOFF_BASE_SECONDS", "0.5")
    if base < 0:
        raise ValueError("BACKOFF_BASE_SECONDS must be >= 0")
    return Settings(url, timeout, retries, base)


settings = load_settings()   # an invalid value stops the service at startup


def backoff_delay(retry_number: int, base: float) -> float:
    """Wait before retry 1, 2, 3 ... = base, 2*base, 4*base ... (0.5, 1.0, 2.0)."""
    return base * 2 ** (retry_number - 1)


client: httpx.AsyncClient


@asynccontextmanager
async def lifespan(_app):
    global client
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        yield


app = FastAPI(title="Exam Scheduler Service", lifespan=lifespan)


# ---------- Retrying HTTP helper ----------
class BookingServiceUnavailable(Exception):
    """All attempts failed with a retryable error (timeout / connection / 5xx)."""


class UnexpectedResponse(Exception):
    """The booking service answered with a status this service cannot handle."""


class Attempts:
    """Counts the retries used by one POST /exams request."""
    retries = 0


async def call_booking_service(method, path, attempts, **kwargs):
    """Call the Room Booking Service; retry timeouts, connection errors and 5xx.

    4xx answers (404, 409, 422 ...) are real answers, not transient failures,
    so they are returned immediately and never retried.
    """
    op = f"{method} {path}"
    total = settings.max_retries + 1
    failure = ""
    for attempt in range(1, total + 1):
        try:
            response = await client.request(
                method, settings.room_booking_service_url + path, **kwargs)
            if response.status_code < 500:
                log.info("%s attempt %d/%d -> HTTP %d", op, attempt, total, response.status_code)
                return response
            failure = f"HTTP {response.status_code}"
        except httpx.TransportError as exc:   # includes all timeouts
            failure = type(exc).__name__      # e.g. ReadTimeout, ConnectError
        if attempt < total:
            wait = backoff_delay(attempt, settings.backoff_base_seconds)
            log.info("%s attempt %d/%d failed: %s | backoff %.2fs, will retry",
                     op, attempt, total, failure, wait)
            attempts.retries += 1
            await asyncio.sleep(wait)         # non-blocking
        else:
            log.info("%s attempt %d/%d failed: %s | no attempts left, giving up",
                     op, attempt, total, failure)
    raise BookingServiceUnavailable(f"{op} failed after {total} attempts ({failure})")


# ---------- Exam model ----------
class ExamIn(BaseModel):
    exam_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", examples=["CS101-final"])
    course_code: str
    date: Date
    start_hour: int = Field(ge=OPEN_HOUR, le=CLOSE_HOUR)
    end_hour: int = Field(ge=OPEN_HOUR, le=CLOSE_HOUR)
    num_students: int = Field(gt=0)

    @field_validator("course_code")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @model_validator(mode="after")
    def start_before_end(self):
        if self.start_hour >= self.end_hour:
            raise ValueError("start_hour must be less than end_hour")
        return self


def purpose_of(exam: ExamIn) -> str:
    return f"Exam {exam.exam_id} - {exam.course_code} ({exam.num_students} students)"


def same_exam(booking: dict, exam: ExamIn) -> bool:
    """Does this existing booking describe exactly this exam request?"""
    return (booking["date"] == exam.date.isoformat()
            and booking["start_hour"] == exam.start_hour
            and booking["end_hour"] == exam.end_hour
            and booking["purpose"] == purpose_of(exam))


def scheduled(status, exam, booking, attempts):
    return JSONResponse(status_code=status, content={
        "exam_id": exam.exam_id, "course_code": exam.course_code,
        "date": exam.date.isoformat(), "start_hour": exam.start_hour,
        "end_hour": exam.end_hour, "num_students": exam.num_students,
        "booking_id": booking["booking_id"], "room_id": booking["room_id"],
        "retries_used": attempts.retries})


def error(status, detail, attempts):
    return JSONResponse(status_code=status,
                        content={"detail": detail, "retries_used": attempts.retries})


# ---------- Endpoints ----------
@app.get("/config")
def get_config():
    return {"room_booking_service_url": settings.room_booking_service_url,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "max_retries": settings.max_retries,
            "backoff_base_seconds": settings.backoff_base_seconds}


@app.post("/exams")
async def schedule_exam(exam: ExamIn):
    """Schedule an exam: 201 new, 200 identical repeat, 409 no room / ID clash,
    503 booking service unavailable, 422 invalid input."""
    attempts = Attempts()
    try:
        return await schedule(exam, attempts)
    except BookingServiceUnavailable as exc:
        log.info("exam %s: FAILED, %s", exam.exam_id, exc)
        return error(503, f"Room Booking Service unavailable: {exc}. "
                          "The booking may or may not have been created; "
                          "repeat the same request to find out.", attempts)
    except UnexpectedResponse as exc:
        log.info("exam %s: FAILED, %s", exam.exam_id, exc)
        return error(502, str(exc), attempts)


async def schedule(exam: ExamIn, attempts: Attempts):
    # Stable ID: every retry or repeat of this exam uses the same booking ID.
    booking_id = f"exam-{exam.exam_id}"

    # 1. Was this exam already scheduled (e.g. an earlier attempt whose reply was lost)?
    r = await call_booking_service("GET", f"/bookings/{booking_id}", attempts)
    if r.status_code == 200:
        existing = r.json()
        if same_exam(existing, exam):
            log.info("exam %s: already scheduled in %s, returning it", exam.exam_id,
                     existing["room_id"])
            return scheduled(200, exam, existing, attempts)
        return error(409, f"Exam ID '{exam.exam_id}' is already scheduled with different "
                          "details", attempts)
    if r.status_code != 404:
        raise UnexpectedResponse(f"GET booking returned HTTP {r.status_code}")

    # 2. Rooms big enough, smallest first (room ID breaks ties).
    r = await call_booking_service("GET", "/rooms", attempts)
    if r.status_code != 200:
        raise UnexpectedResponse(f"GET rooms returned HTTP {r.status_code}")
    eligible = sorted((room for room in r.json() if room["capacity"] >= exam.num_students),
                      key=lambda room: (room["capacity"], room["room_id"]))
    if not eligible:
        return error(409, f"No room is large enough for {exam.num_students} students",
                     attempts)

    # 3. Try each room; the booking service decides whether it is free.
    body = {"date": exam.date.isoformat(), "start_hour": exam.start_hour,
            "end_hour": exam.end_hour, "purpose": purpose_of(exam)}
    for room in eligible:
        r = await call_booking_service("PUT", f"/bookings/{booking_id}", attempts,
                                       json={"room_id": room["room_id"], **body})
        if r.status_code in (200, 201):
            log.info("exam %s: booked room %s (HTTP %d)", exam.exam_id, room["room_id"],
                     r.status_code)
            return scheduled(r.status_code, exam, r.json(), attempts)
        if r.status_code == 409:
            # Room taken, OR a concurrent identical request already booked this exam.
            check = await call_booking_service("GET", f"/bookings/{booking_id}", attempts)
            if check.status_code == 200:
                existing = check.json()
                if same_exam(existing, exam):
                    return scheduled(200, exam, existing, attempts)
                return error(409, f"Exam ID '{exam.exam_id}' is already scheduled with "
                                  "different details", attempts)
            log.info("exam %s: room %s is not free, trying the next one",
                     exam.exam_id, room["room_id"])
            continue
        if r.status_code == 404:
            continue    # room disappeared; try the next one
        raise UnexpectedResponse(f"PUT booking returned HTTP {r.status_code}")

    return error(409, "No free room large enough for this date and time", attempts)
