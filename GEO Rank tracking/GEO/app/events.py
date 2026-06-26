import asyncio
import json
import time
import logging
from collections import deque

log = logging.getLogger("geo.events")

_subscribers: list[asyncio.Queue] = []
_recent: deque = deque(maxlen=50)


def _make_event(event_type: str, data: dict) -> dict:
    evt = {"type": event_type, "ts": time.time(), **data}
    return evt


def broadcast(event_type: str, **data):
    evt = _make_event(event_type, data)
    _recent.append(evt)
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.remove(q)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    for evt in _recent:
        if evt.get("type") == "notification":
            continue
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            break
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    if q in _subscribers:
        _subscribers.remove(q)


def get_recent() -> list[dict]:
    return list(_recent)
