# Pulse Agent — context, memory, and the framework question

> **STATUS (2026-06-22): DEFERRED.** Pulse agent work is post-backend.

> The recruiter chat ("Pulse") is the **only** live agent in the product. This doc
> captures: (1) what it actually does today, (2) the industry-standard way to
> handle context/memory, (3) a drop-in replacement for the crude part, and (4) the
> loop-vs-LangGraph decision. Part of the SP5/SP6 Pulse hardening; safe to pick up
> independently this weekend.

---

## 1. What it is (clearing up the confusion)

Pulse is a **plain tool-calling loop + LiteLLM**, not LangGraph. There is **zero
LangGraph anywhere** in the backend (verified: no `StateGraph`, no `graph.py`).

- The loop: `recruiter_agent/runner.py` `run_recruiter_turn` (~789-1165), max 5
  tool hops. One model (`llm_model_fast`), one system prompt (`prompts.py`).
- This is the exact same shape as the Vercel AI SDK's `streamText({ tools,
  maxSteps })` — that SDK just hides the loop. Nothing new to learn.
- **Keep it a loop.** The only stateful bit is the confirm-gate (halt → Redis →
  resume), already handled. LangGraph would add weight for nothing. If you want
  typed-tool ergonomics later, **Pydantic AI** is the light option — not LangGraph.

### Stale docs to fix
`CLAUDE.md` ("Chat Agent LangGraph — V2 candidate chat") and the entire "V2 Chat
Path" in `prompts.md` describe a **candidate-facing LangGraph chat that no longer
exists** — its graph files are gone and migration `0030_drop_chat_v2_tables.py`
drops its tables. Screening is now voice-based. **Action:** correct those docs and
fold the leftover `backend/src/agent/` helpers (`generators.py`,
`prompts/tailored_qs.py`, `prompts/extract.py`) into wherever they're actually
called (role drafting / assignment gen), or delete if dead.

---

## 2. Current context/memory handling — the crude reality

Verified in code:

| Concern | Today | Where |
|---|---|---|
| Conversation history | rebuilt from DB rows per turn, bounded crudely | `runner.py:639-786` `_build_history_for_llm` |
| Long-term memory | per-recruiter key-value store, **written only on explicit "remember"** | `schemas.py:459`, `tools.py:1578` |
| Memory injection | **dumped as a flat `1200`-CHARACTER blob** into the system prompt | `runner.py:547-548` `max_chars=1200` |
| **Rolling summary** | **none** | — |
| Token budgeting | **none** (char-based only) | — |
| Relevance retrieval | **none** (whole blob, every turn) | — |

> The `summari*` mentions in `runner.py` are **not** memory summaries:
> `:150-179` names the conversation *title* (3-5 words), `:1139` is a final pass
> when the hop limit is hit. There is **no conversation summarization happening.**

So: char-truncation of a manually-written fact store. None of the three standard
memory layers exist.

---

## 3. The industry standard (3 layers)

Measure in **tokens, never characters.**

1. **Short-term (this conversation):** token-budgeted sliding window + **rolling
   summary** (the "conversation summary buffer"). Keep recent turns verbatim; when
   over budget, summarize the overflow into a running summary and drop the raw
   turns.
2. **Long-term (across conversations):** a **retrieval store** (SQL + vector),
   pulled in **by relevance** — not stuffed wholesale. You already have pgvector.
3. **Agent context:** keep the always-on prompt small; **fetch context via tools**
   on demand; cache the stable system-prompt prefix.

There's one standard *practice*; no mandatory library. Hand-rolling the summary
buffer is normal and fine. Managed memory layers (Mem0 / Zep / Letta) exist if you
want them, but pgvector + ~15 lines covers it.

---

## 4. Drop-in replacement

### 4.1 Where the rolling summary lives
Add a `summary` key to the existing `recruiter_conversations.state` JSONB (no
migration needed). It holds the running summary of folded-out turns.

### 4.2 `_build_history_for_llm` — token-budgeted + summary
```python
import litellm
from litellm import trim_messages

MODEL = settings.llm_model_fast
HISTORY_BUDGET = 6000      # tokens for history (tune to model window minus response)
KEEP_RECENT     = 20       # turns kept verbatim before summarizing

async def build_history_for_llm(session, conv, rows) -> list[dict]:
    system = [{"role": "system", "content": SYSTEM_PROMPT}]

    summary = (conv.state or {}).get("summary")
    if summary:
        system.append({"role": "system",
                       "content": f"Summary of earlier conversation:\n{summary}"})

    recent = rows_to_messages(rows[-KEEP_RECENT:])
    msgs = system + recent

    # over budget? fold the older turns into the rolling summary, persist, rebuild
    if litellm.token_counter(model=MODEL, messages=msgs) > HISTORY_BUDGET:
        older = rows_to_messages(rows[:-KEEP_RECENT])
        if older:
            new_summary = await summarize_turns(summary, older)
            conv.state = {**(conv.state or {}), "summary": new_summary}
            await session.flush()                       # persist on the conversation
            system = [{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "system",
                       "content": f"Summary of earlier conversation:\n{new_summary}"}]
            msgs = system + recent

    # final safety net: hard-trim to the model window, preserving system msgs
    return trim_messages(msgs, model=MODEL, max_tokens=HISTORY_BUDGET)
```

### 4.3 `summarize_turns` — one cheap call
```python
async def summarize_turns(prev_summary: str | None, older: list[dict]) -> str:
    body = render_for_summary(older)           # plain text of the old turns
    prompt = (
        "You maintain a running summary of a recruiter's chat with an AI hiring "
        "assistant. Update the summary to include the new turns. Keep durable "
        "facts, decisions, and open threads; drop chit-chat. <= 200 words.\n\n"
        f"Current summary:\n{prev_summary or '(none)'}\n\nNew turns:\n{body}"
    )
    resp = await litellm.acompletion(
        model=settings.llm_model_fast,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300, temperature=0.2,
    )
    return resp.choices[0].message.content.strip()
```

### 4.4 Memory → a `recall` tool (replace the 1200-char dump)
Delete the `snapshot_for_prompt(..., max_chars=1200)` injection at
`runner.py:547-548`. Instead register a read tool (it's exempt from the confirm
gate, like the other reads):
```python
async def recall_memory(query: str) -> str:
    """Recall this recruiter's stored preferences/facts relevant to `query`."""
    rows = await memory_repo.search(session, actor_hash=actor_hash, query=query)  # pgvector
    return "\n".join(f"- {r.key}: {r.value}" for r in rows) or "(nothing stored)"
```
Add its schema next to the other tools in `schemas.py`. The model now pulls memory
**only when relevant**, instead of paying for a 1200-char blob every turn.
(`recruiter_memory` already exists; add a `search` that does a pgvector similarity
query — embed entries on write.)

---

## 5. Implementation plan

1. Add `summary` to `recruiter_conversations.state` usage (no migration).
2. Replace `_build_history_for_llm` with §4.2; add `summarize_turns` (§4.3).
3. Remove the `max_chars=1200` memory injection (`runner.py:547-548`).
4. Add `recall_memory` tool + schema; embed memory entries on write for pgvector.
5. Tune `HISTORY_BUDGET` / `KEEP_RECENT` to the model window.
6. (Optional) wrap tools in **Pydantic AI** for typed ergonomics — not required.

## Acceptance criteria

- No character-based truncation anywhere in the Pulse path (grep `max_chars`).
- History is budgeted by `litellm.token_counter`; long chats produce a populated
  `state.summary` and stay within the window.
- Memory is fetched via `recall_memory` on demand, not pre-injected.
- A 100-turn conversation still fits the model window and stays coherent.

## Doc neighbours
`04-sp3-operational-surface.md` (Pulse as command surface, `/conversations/[id]`),
`07-hardening-polish.md` (SP6 Pulse state-machine bugs: cancel re-trigger,
`edited_args` injection, confirm lifecycle).
