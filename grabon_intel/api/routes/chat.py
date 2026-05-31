"""POST /chat — SSE stream of ChatEvent."""
from __future__ import annotations

from typing import AsyncIterator

import orjson
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ...chat import ChatRequest, run_chat

router = APIRouter(prefix="/chat", tags=["chat"])


class _HistoryTurn(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    message: str
    user: str = "anonymous"
    history: list[_HistoryTurn] = []


@router.post("")
async def chat(body: ChatBody) -> EventSourceResponse:
    history = [{"role": h.role, "content": h.content} for h in body.history[-10:]]

    async def gen() -> AsyncIterator[dict]:
        async for ev in run_chat(ChatRequest(message=body.message, user=body.user, history=history)):
            yield {"event": ev.type, "data": orjson.dumps(ev.data).decode("utf-8")}

    return EventSourceResponse(gen())
