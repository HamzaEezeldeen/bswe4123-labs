"""Tiny fake Room Booking Service used only by the retry-policy tests.

STUB_MODE=500  every request returns HTTP 500
STUB_MODE=404  no booking exists; one room (cap 50); PUT answers 404
STUB_MODE=409  no booking exists; one room (cap 50); PUT answers 409
GET /_hits shows how many requests of each kind were received.

Run:  python -m uvicorn stub_service:app --port 8013
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MODE = os.environ.get("STUB_MODE", "500")
app = FastAPI()
hits = {}


@app.get("/_hits")
def get_hits():
    return hits


@app.api_route("/{path:path}", methods=["GET", "PUT", "POST", "DELETE"])
def anything(request: Request, path: str):
    key = f"{request.method} /{path}"
    hits[key] = hits.get(key, 0) + 1
    if MODE == "500":
        return JSONResponse({"detail": "stub failure"}, status_code=500)
    if request.method == "GET" and path.startswith("bookings/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    if request.method == "GET" and path == "rooms":
        return [{"room_id": "S50", "building": "Stub", "capacity": 50}]
    return JSONResponse({"detail": "stub says " + MODE}, status_code=int(MODE))
