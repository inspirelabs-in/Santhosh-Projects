"""REJECTION_MESSAGE_V1 -- Stage 8a candidate rejection body.



The prompt returns ONLY the body text (no subject, greeting, or sign-off).

The wrapping activity validates via `RejectionDraft` which stores the

`category_used` alongside. The safe-reasons library lives in

src/services/rejection.py (not here) so we don't duplicate that source of truth.

"""



REJECTION_MESSAGE_VERSION = "v6"  # Langfuse version 6
REJECTION_MESSAGE_V1 = """Draft a rejection email for a job candidate. Be respectful, specific (but safe), and brief.



Candidate name: {candidate_name}

Role applied for: {role_title}

Rejection category: {rejection_category}

Rejection reason template: {reason_template}

Company name: {company_name}



Tone: Direct, warm, human. No corporate HR language. Write like a real person who genuinely cares.



Rules:

- Keep it under 120 words

- Be warm but direct, no corporate filler

- Reference the specific role they applied for

- Give one specific-but-safe reason (from the template provided)

- Invite them to opt into the talent pool for future roles

- Encourage them to keep building and learning

- Do NOT mention any scores, rankings, or internal assessments

- Do NOT compare them to other candidates

- Do NOT use phrases like "unfortunately" or "regret to inform"

- Do NOT use corporate buzzwords or template language



Respond with a JSON object in this exact shape:

{{

  "body": "the email body text",

  "category_used": "{rejection_category}"

}}"""

