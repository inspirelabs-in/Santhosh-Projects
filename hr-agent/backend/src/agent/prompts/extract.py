"""Prompt: extract structured screening fields from a free-form candidate turn.

Runs on the FAST model after every user message. Pulls any of the 6 target
fields the candidate volunteered (CTC current, CTC expected, notice days,
relocate willingness, plus answers to q1/q2). Idempotent -- only emit
fields that are clearly stated. Never guess.
"""

EXTRACT_TURN_VERSION = "v1"

EXTRACT_TURN_V1 = """You extract structured screening data from a candidate's chat reply. NEVER guess or infer; only emit fields the candidate clearly stated.

Already-captured fields (do not re-extract these):
{already_captured_json}

Open tailored questions still pending:
{pending_questions_json}

Latest candidate message:
\"\"\"{candidate_message}\"\"\"

Recent context (last 4 turns):
{recent_history}

Rules:
- Numeric CTCs in INR LPA. If user says "12 lakhs" emit 12. If "15 LPA" emit 15. If unspecified currency or unit, leave null.
- ``notice_period_days``: integer days. Convert: "1 month" -> 30, "60 days" -> 60, "immediate" -> 0. Leave null if vague ("ASAP", "depends").
- ``willing_to_relocate``: true / false / null only. Conditional answers ("if package is right") -> null.
- ``tailored_a1`` / ``tailored_a2``: only emit if the candidate's reply substantively addresses the corresponding pending question. Verbatim quote of their answer (trim to 1500 chars). Never paraphrase.
- ``intent``: one of:
    - "answer"            (replied to a pending question)
    - "ask_clarification" (candidate asked a question instead of answering)
    - "out_of_scope"      (chit-chat, off-topic)
    - "request_pause"     (wants to come back later)
- ``next_question_to_ask``: one of [q1, q2, ctc_current, ctc_expected, notice, relocate, none]. Pick the FIRST not-yet-captured field in that order. "none" only if all 6 captured.

Output strict JSON only:
{{
  "tailored_a1": "...",  // string or null
  "tailored_a2": null,
  "current_ctc_lpa": null,  // number or null
  "expected_ctc_lpa": null,
  "notice_period_days": null,  // integer or null
  "willing_to_relocate": null,  // true/false/null
  "intent": "answer",
  "next_question_to_ask": "q2"
}}"""
