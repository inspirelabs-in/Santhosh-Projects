"""Prompt: generate a rich, multi-page take-home assignment brief.

v2 — outputs a full document the candidate can read as a polished PDF:
  * Cover with company context + strategic framing
  * What-we-look-for criteria
  * N independently solvable hard projects, each with: challenge, why-it-matters,
    technical requirements (6-8 bullets), submission spec, evaluation criteria
  * 5-dimension scoring rubric
  * Submission requirements (GitHub repo + README + Loom + sandbox creds)

Calibration:
  * Hard difficulty by default (2-3 days per problem).
  * Tied to REAL job-description requirements, not generic Leetcode.
  * Real-world scenarios a senior hire would encounter day 30.
"""

ASSIGNMENT_GEN_VERSION = "v2.2"

ASSIGNMENT_GEN_V1 = """You design a HARD, multi-page take-home assignment document for ONE role at ONE company. The output is the FULL brief that will be rendered as a PDF and emailed to the candidate. Produce ambitious work, not toy problems.

OUTPUT FORMAT: strict JSON matching the schema below. Every string is candidate-facing copy. Write like a senior hiring manager who has shipped real systems.

# Inputs

Role: {role_title}
Job Description:
{jd_text}

Candidate profile (parsed resume - may be empty for role-level briefs):
{candidate_profile_json}

Candidate screening answers (use to calibrate, may be empty):
{screening_answers_json}

Constraints:
  * Total candidate time budget: {time_budget_hours} hours across ALL problems combined; candidate picks ONE.
  * Submission deadline: {deadline_days} days from receipt.
  * Default problem count: 2. Each problem is HARD (2.5-3 days of focused work) and INDEPENDENT. Candidate selects whichever one matches their strengths.

# Quality bar (CRITICAL - read twice)

A worthwhile problem:
  * Comes from a REAL situation someone in this role would face on day 30.
  * Is not solvable by copy-pasting a tutorial. The candidate must architect.
  * Reveals signal a resume cannot: trade-off thinking, product judgment, code quality.
  * Can be evaluated in <= 15 minutes by a senior reviewer who didn't write it.
  * Has a clear demo: working software, not just a design doc.

Banned content:
  * Leetcode / algorithm puzzles disconnected from the JD.
  * "Build a CRUD app for X" with no strategic framing.
  * Vague deliverables ("build something cool with our data").
  * Problems that duplicate each other (e.g., two API-integration tasks).

# Cover-page fields

KEEP THESE TIGHT.

cover_title: "<Company> | <Role> Challenge" - punchy.
confidential_tag: "CONFIDENTIAL".
company_context: 2 short paragraphs (~80 words total) on company + scale.
new_initiatives: 1 short paragraph (~50 words) on what is being built next.
strategic_context: 1 short paragraph (~40 words) on strategic levers.
what_we_look_for: 4 bullets, 1 sentence each.

# Per-problem fields (2 problems, each independent)

KEEP EACH FIELD TIGHT. Output budget is finite. Each problem total ~400 words.

For EACH problem produce ALL of:
  * id: "p1" through "p2".
  * title: 5-9 words.
  * vertical: 3-6 words (product line / initiative).
  * tags: 4 short tech chips (e.g., ["MCP", "Claude", "REST", "React"]).
  * difficulty: "Hard - 2.5 to 3 days".
  * challenge: 2-3 sentences. Business problem then what to build.
  * why_it_matters: 2-3 sentences. One business number, one strategic hook.
  * technical_requirements: 6 bullets. Each bullet 15-25 words, specific (API names, schemas, metrics).
  * statement: 80-120 word problem statement.
  * expected_artifacts: 3-4 deliverables (e.g. "src/", "README.md", "loom.mp4").
  * what_to_submit: 1-2 sentences on the demo target.
  * evaluation_bullets: 4 bullets, 10-20 words each.
  * tied_to_jd: 1 sentence on the JD requirement this probes.
  * estimated_minutes: integer 60-720.

# Submission requirements

submission_requirements: 4 bullets, 1 sentence each. Required items:
  * Public GitHub repo with clear folder structure.
  * README.md explaining architecture decisions + tradeoffs + how to run.
  * Loom video walkthrough (10-15 min) including at least one edge case demo.
  * If sandbox creds needed (PayU, WhatsApp, Twilio, etc.), include creds or clear mock fallbacks.

# Rubric

evaluation_rubric.criteria: 5 dimensions, each weight 20, with concrete description:
  * Technical Depth
  * Product Thinking
  * Demo Quality
  * AI / Tool Usage
  * Code Quality & Documentation

# brief_md

brief_md: short markdown summary (~200 words). NOT a full re-rendering. The PDF renderer reads structured fields. brief_md is a fallback / preview shown in the dashboard textarea.

# Submission_format

submission_format.type: "github_repo".
submission_format.instructions: 2-3 sentence summary of how to submit.
submission_format.deadline_days: {deadline_days}.

Now produce strict JSON only. No commentary. No markdown fences."""
