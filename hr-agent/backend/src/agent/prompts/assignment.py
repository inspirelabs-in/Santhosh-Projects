"""Prompt: generate take-home assignment brief.

Split into two parts:
  Part 1 — Company persona ({company_persona}, injected from the org row in DB)
  Part 2 — Problem statement generation (driven by JD + role)

The company persona and name are NOT hardcoded: ``gen_assignment`` fetches them
from ``organizations`` (name + settings.org_context.summary + hiring_persona)
and passes ``{company_persona}`` / ``{company_name}`` to ``compile_prompt``. The
model reads the JD, extracts the actual skills/domain/challenges, and generates
problems that test THOSE specific requirements. No hardcoded project names.
"""

ASSIGNMENT_GEN_VERSION = "v9"  # role-level only: dropped candidate/screening inputs

# ─── Part 1 + Part 2: persona is injected at compile time ────────

ASSIGNMENT_GEN_V1 = """{company_persona}

# Your Task

Design a take-home assignment for the role below. The output is a polished brief that will be rendered as a PDF and emailed to the candidate.

# CRITICAL: READ THE JD FIRST

The JD below contains the ACTUAL skills, tools, domain, and challenges for this role. Your assignment problems MUST test what the JD asks for. Do NOT default to generic engineering problems.

STEP 1 — Extract from the JD:
  * Primary function: engineering / product / design / marketing / sales / ops / finance / HR / legal / support
  * Domain: what business area does this role operate in?
  * Key tools/technologies mentioned
  * Key outcomes/responsibilities mentioned
  * Seniority level

{user_brief_override}

STEP 2 — Generate problems that directly test JD requirements:

  FOR TECHNICAL ROLES (engineering, data, ML, DevOps, SRE, QA):
    Problems should involve building working software. Use the EXACT tech stack from the JD.
    Example signals from JD → problem type:
      "React + Node.js" → build a frontend feature with API
      "Python + ML" → build a model pipeline with evaluation
      "DevOps + Kubernetes" → design and implement infra automation
      "Data engineering + Spark" → build a data pipeline with transformations
      "Mobile + React Native" → build a mobile feature with offline support
    Ground the scenario in {company_name}'s domain where natural, but the TECH must match the JD.

  FOR NON-TECHNICAL ROLES (marketing, sales, HR, finance, ops, design, PM, legal):
    Problems should involve analysis, strategy, or deliverables relevant to the function.
    Example signals from JD → problem type:
      "Growth marketing" → design a user acquisition campaign with budget allocation
      "Product management" → write a PRD for a feature with prioritization framework
      "UX design" → redesign a user flow with wireframes and rationale
      "Sales" → build a territory plan or outbound strategy
      "Finance" → build a financial model or variance analysis
      "HR" → design an onboarding program or comp benchmarking exercise
      "Content" → create a content calendar with distribution strategy
    Use {company_name} as the business context, but the WORK must match what the JD describes.

  FOR HYBRID ROLES (technical PM, data analyst, UX researcher, growth engineer):
    One problem should be analytical/strategic, one should involve hands-on execution.
    Match to the specific blend described in the JD.

STEP 3 — Verify before outputting:
  * Does each problem test skills explicitly listed in the JD? If not, rewrite.
  * Would someone who read the JD recognize these problems as relevant? If not, rewrite.
  * Are the two problems DIFFERENT in what they test? If they overlap, replace one.

# Inputs

This brief is generated at the ROLE level, before any candidate applies. There is NO candidate data. Base everything on the JD and role below; never reference a specific candidate, resume, or prior answers.

{user_brief_section}

Role: {role_title}

Job Description (THIS IS YOUR PRIMARY INPUT — base ALL problems on this):
{jd_text}

Constraints:
  * Total candidate time budget: {time_budget_hours} hours across ALL problems combined; candidate picks ONE.
  * Submission deadline: {deadline_days} days from receipt.
  * Problem count: 2. Each problem is HARD and INDEPENDENT. Candidate selects whichever matches their strengths.

# Quality Bar

A worthwhile problem:
  * Tests skills the JD ACTUALLY asks for, not skills you inferred or imagined.
  * Is not solvable by copy-pasting a tutorial. The candidate must architect.
  * Reveals signal a resume cannot: trade-off thinking, judgment, execution quality.
  * Can be evaluated in <= 15 minutes by a senior reviewer.
  * Has a clear deliverable: working software (technical) or polished document/analysis (non-technical).

Banned:
  * Leetcode / algorithm puzzles disconnected from the JD.
  * "Build a CRUD app" with no strategic framing.
  * Vague deliverables ("build something cool").
  * Two problems that test the same skill set.
  * Generic company names. Always use {company_name}.
  * Repeating the same problem across different roles. Each role's JD is unique; problems must be unique.

# Text Rules
  * ALL text must be PLAIN TEXT only. No HTML tags. No HTML entities.
  * The company is ALWAYS "{company_name}". Never invent names.
  * Write like a senior hiring manager who has shipped real systems.

# Cover-page fields

cover_title: "{company_name} | {role_title} Challenge" — exactly this format.
confidential_tag: "CONFIDENTIAL".
company_context: 2 short paragraphs (~80 words) about {company_name}. What it does, scale, culture.
new_initiatives: 1 short paragraph (~50 words). Infer what {company_name} is building from the JD's domain. If JD mentions ML, talk about AI initiatives. If JD mentions growth, talk about user acquisition. Match the JD.
strategic_context: 1 short paragraph (~40 words). Strategic levers relevant to THIS role's function.
what_we_look_for: 4 bullets, 1 sentence each. Reflect qualities the JD asks for.

# Per-problem fields (2 problems)

For EACH problem produce ALL of:
  * id: "p1" or "p2".
  * title: 5-9 words. Derived from JD requirements, grounded in {company_name} context.
  * vertical: 3-6 words (the business area this problem maps to).
  * tags: 4 short tech/skill chips. MUST match skills from the JD.
  * difficulty: "Hard — 2.5 to 3 days".
  * challenge: 2-3 sentences. Business problem at {company_name} tied to this role, then what to build.
  * why_it_matters: 2-3 sentences. One business impact, one strategic hook.
  * technical_requirements: 6 bullets. Each 15-25 words. For technical roles: specific APIs, schemas, metrics. For non-technical: specific deliverables, frameworks, analysis methods.
  * statement: 80-120 word problem statement.
  * expected_artifacts: 3-4 deliverables.
  * what_to_submit: 1-2 sentences on the demo/deliverable target.
  * evaluation_bullets: 4 bullets, 10-20 words each.
  * tied_to_jd: 1 sentence quoting the JD requirement this problem tests.
  * estimated_minutes: integer 60-720.

# Submission requirements

submission_requirements: 4 bullets. For technical roles: GitHub repo + README + Loom video + working demo. For non-technical roles: document/presentation + executive summary + rationale writeup + any supporting data/models.

# Rubric

evaluation_rubric.criteria: an ARRAY of 5 OBJECTS. Each object MUST have exactly these keys:
  * name: the dimension name (string).
  * weight: integer, all five sum to 100 (use 20 each).
  * description: 1 sentence on what a strong answer on this dimension looks like.
Do NOT output bare strings. Each criterion is an object, e.g.:
  {"name": "Technical Depth", "weight": 20, "description": "Handles edge cases and scale, not just the happy path."}

Dimension names to use:
For technical roles: Technical Depth, Product Thinking, Demo Quality, AI/Tool Usage, Code Quality & Documentation.
For non-technical roles: Strategic Thinking, Analytical Rigor, Communication Quality, Creativity & Insight, Practical Feasibility.
For hybrid roles: mix dimensions from both.

# brief_md

brief_md: short markdown summary (~200 words). NOT a full re-rendering. Preview for dashboard textarea.

# Submission format

submission_format.type: "github_repo" for technical, "zip_upload" for non-technical.
submission_format.instructions: 2-3 sentences on how to submit.
submission_format.deadline_days: {deadline_days}.

Now produce strict JSON only. No commentary. No markdown fences."""
