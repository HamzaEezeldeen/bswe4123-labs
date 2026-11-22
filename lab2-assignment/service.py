"""Campus Room Booking Service (FastAPI + Pydantic + Uvicorn).

Run:  python -m uvicorn service:app --port 8000
Storage is in memory: data is lost when the server stops.
"""
import threading
from datetime import date as Date

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator

app = FastAPI(title="Campus Room Booking Service")

OPEN_HOUR, CLOSE_HOUR = 8, 20


# ---------- Models ----------
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
    """Body of PUT /bookings/{booking_id}. The booking ID comes from the URI."""
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


# ---------- In-memory storage ----------
rooms: dict[str, RoomIn] = {}
bookings: dict[str, Booking] = {}

# One lock protects "check for conflicts + insert" so two concurrent requests
# cannot both pass the check. It only works inside ONE server process.
lock = threading.Lock()


def overlaps(a: BookingIn, b: BookingIn) -> bool:
    """Same room, same date and overlapping hours (touching ends do not overlap)."""
    return (a.room_id == b.room_id and a.date == b.date
            and a.start_hour < b.end_hour and b.start_hour < a.end_hour)


# ---------- Rooms ----------
@app.get("/rooms")
def list_rooms():
    with lock:
        return list(rooms.values())


@app.post("/rooms", status_code=201)
def create_room(room: RoomIn):
    with lock:
        if room.room_id in rooms:
            raise HTTPException(409, f"Room '{room.room_id}' already exists")
        rooms[room.room_id] = room
    return room


@app.get("/rooms/{room_id}")
def get_room(room_id: str):
    with lock:
        room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"Room '{room_id}' not found")
    return room


# ---------- Bookings ----------
@app.get("/bookings")
def list_bookings():
    with lock:
        return list(bookings.values())


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    with lock:
        booking = bookings.get(booking_id)
    if booking is None:
        raise HTTPException(404, f"Booking '{booking_id}' not found")
    return booking


@app.put("/bookings/{booking_id}", response_model=Booking)
def put_booking(booking_id: str, data: BookingIn, response: Response):
    """Idempotent create. The client chooses booking_id.

    new ID                      -> 201 Created
    same ID, identical payload  -> 200 OK (nothing is created again)
    same ID, different payload  -> 409 (bookings are immutable)
    other ID, overlapping slot  -> 409
    """
    with lock:
        existing = bookings.get(booking_id)
        if existing is not None:
            if BookingIn(**existing.model_dump(exclude={"booking_id"})) == data:
                response.status_code = 200
                return existing
            raise HTTPException(
                409, f"Booking '{booking_id}' already exists with different details")
        if data.room_id not in rooms:
            raise HTTPException(404, f"Room '{data.room_id}' not found")
        for other in bookings.values():
            if overlaps(data, other):
                raise HTTPException(
                    409, f"Room {data.room_id} is already booked "
                         f"{other.start_hour}:00-{other.end_hour}:00 on {other.date} "
                         f"(booking '{other.booking_id}')")
        booking = Booking(booking_id=booking_id, **data.model_dump())
        bookings[booking_id] = booking
        response.status_code = 201
        return booking


@app.delete("/bookings/{booking_id}", status_code=204)
def delete_booking(booking_id: str):
    with lock:
        if bookings.pop(booking_id, None) is None:
            raise HTTPException(404, f"Booking '{booking_id}' not found")
    return Response(status_code=204)
