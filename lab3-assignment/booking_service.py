"""Room Booking Service for Lab 3 (adapted from Lab 2).

Same API contract and status codes as Lab 2, with two changes:
  1. Bookings live in a SQLite file, so they survive a restart.
  2. An optional artificial response delay (to simulate a slow service).

Environment variables:
  BOOKING_DB_PATH                  SQLite file (default: bookings.db)
  BOOKING_SERVICE_DELAY_SECONDS    delay added to each response (default: 0)
  BOOKING_SERVICE_DELAY_METHODS    HTTP methods that are delayed
                                   (default: GET,POST,PUT,DELETE)

Run:  python -m uvicorn booking_service:app --port 8005
"""
import asyncio
import os
import sqlite3
from contextlib import contextmanager
from datetime import date as Date

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

DB_PATH = os.environ.get("BOOKING_DB_PATH", "bookings.db")
DELAY = float(os.environ.get("BOOKING_SERVICE_DELAY_SECONDS", "0"))
DELAY_METHODS = {m.strip().upper() for m in
                 os.environ.get("BOOKING_SERVICE_DELAY_METHODS", "GET,POST,PUT,DELETE").split(",")}
if DELAY < 0:
    raise ValueError("BOOKING_SERVICE_DELAY_SECONDS must be >= 0")

OPEN_HOUR, CLOSE_HOUR = 8, 20

app = FastAPI(title="Room Booking Service (Lab 3)")


@app.middleware("http")
async def add_delay(request: Request, call_next):
    """Do the work first, then hold the response back for DELAY seconds.

    This simulates a slow network or an overloaded service: the booking is
    already stored when the client is still waiting (and may time out).
    """
    response = await call_next(request)
    if DELAY > 0 and request.method in DELAY_METHODS and request.url.path != "/health":
        await asyncio.sleep(DELAY)  # non-blocking
    return response


# ---------- Models (same as Lab 2) ----------
class RoomIn(BaseModel):
    room_id: str = Field(examples=["B205"])
    building: str
    capacity: int = Field(gt=0)

    @field_validator("room_id", "building")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class BookingIn(BaseModel):
    room_id: str
    date: Date
    start_hour: int = Field(ge=OPEN_HOUR, le=CLOSE_HOUR)
    end_hour: int = Field(ge=OPEN_HOUR, le=CLOSE_HOUR)
    purpose: str

    @field_validator("room_id", "purpose")
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


class Booking(BookingIn):
    booking_id: str


# ---------- SQLite storage ----------
@contextmanager
def connect():
    # isolation_level=None: we control transactions ourselves (BEGIN IMMEDIATE).
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


with connect() as _con:
    _con.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            room_id TEXT PRIMARY KEY, building TEXT NOT NULL, capacity INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, date TEXT NOT NULL,
            start_hour INTEGER NOT NULL, end_hour INTEGER NOT NULL, purpose TEXT NOT NULL);
    """)


def to_booking(row) -> Booking:
    return Booking(**dict(row))


# ---------- Health ----------
@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Rooms ----------
@app.get("/rooms")
def list_rooms():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM rooms ORDER BY rowid")]


@app.post("/rooms", status_code=201)
def create_room(room: RoomIn):
    with connect() as con:
        try:
            con.execute("INSERT INTO rooms VALUES (?, ?, ?)",
                        (room.room_id, room.building, room.capacity))
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Room '{room.room_id}' already exists")
    return room


@app.get("/rooms/{room_id}")
def get_room(room_id: str):
    with connect() as con:
        row = con.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Room '{room_id}' not found")
    return dict(row)


# ---------- Bookings ----------
@app.get("/bookings")
def list_bookings():
    with connect() as con:
        return [to_booking(r) for r in con.execute("SELECT * FROM bookings ORDER BY rowid")]


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    with connect() as con:
        row = con.execute("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Booking '{booking_id}' not found")
    return to_booking(row)


@app.put("/bookings/{booking_id}", response_model=Booking)
def put_booking(booking_id: str, data: BookingIn, response: Response):
    """Idempotent create (same rules as Lab 2).

    new ID                      -> 201 Created
    same ID, identical payload  -> 200 OK (nothing is created again)
    same ID, different payload  -> 409
    other ID, overlapping slot  -> 409
    unknown room                -> 404

    BEGIN IMMEDIATE takes SQLite's write lock before the checks, so "check for
    conflicts + insert" is one atomic step, even across several processes that
    share the same database file.
    """
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT * FROM bookings WHERE booking_id = ?",
                              (booking_id,)).fetchone()
            if row is not None:
                existing = to_booking(row)
                same = (existing.room_id, existing.date, existing.start_hour,
                        existing.end_hour, existing.purpose) == \
                       (data.room_id, data.date, data.start_hour, data.end_hour, data.purpose)
                if not same:
                    raise HTTPException(
                        409, f"Booking '{booking_id}' already exists with different details")
                response.status_code = 200
                return existing

            if con.execute("SELECT 1 FROM rooms WHERE room_id = ?",
                           (data.room_id,)).fetchone() is None:
                raise HTTPException(404, f"Room '{data.room_id}' not found")

            # overlap: new_start < existing_end AND existing_start < new_end
            clash = con.execute(
                "SELECT * FROM bookings WHERE room_id = ? AND date = ? "
                "AND ? < end_hour AND start_hour < ?",
                (data.room_id, data.date.isoformat(), data.start_hour, data.end_hour)).fetchone()
            if clash is not None:
                raise HTTPException(
                    409, f"Room {data.room_id} is already booked "
                         f"{clash['start_hour']}:00-{clash['end_hour']}:00 on {clash['date']} "
                         f"(booking '{clash['booking_id']}')")

            con.execute("INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?)",
                        (booking_id, data.room_id, data.date.isoformat(),
                         data.start_hour, data.end_hour, data.purpose))
            con.execute("COMMIT")
            response.status_code = 201
            return Booking(booking_id=booking_id, **data.model_dump())
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise


@app.delete("/bookings/{booking_id}", status_code=204)
def delete_booking(booking_id: str):
    with connect() as con:
        if con.execute("DELETE FROM bookings WHERE booking_id = ?",
                       (booking_id,)).rowcount == 0:
            raise HTTPException(404, f"Booking '{booking_id}' not found")
    return Response(status_code=204)
