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

ASSIGNMENT_GEN_VERSION = "v12"  # recruiter brief is the authoritative source of problems + count when present

# ─── Part 1 + Part 2: persona is injected at compile time ────────

ASSIGNMENT_GEN_V1 = """{company_persona}

# Your Task

Design a take-home assignment for the role below. The output is a polished brief that will be rendered as a PDF and emailed to the candidate.

{user_brief_section}

# SOURCE OF PROBLEMS — decide this before anything else

If a "Recruiter's Requirements" section appears ABOVE, it is the AUTHORITATIVE source of the problems:
  * Produce ONE problem per distinct item the recruiter listed. Their count OVERRIDES the requested problem count below — if they wrote "1 assignment", output exactly 1; if they listed 3, output 3.
  * Each problem MUST be the recruiter's described task, expanded into a full brief — same subject and intent, never a substitute. (Recruiter said "data structure visualizer" → the problem is a data structure visualizer, not a generic backend system.)
  * Use the JD and company context ONLY to ground framing, tech stack, and scenario. Never let the JD replace or add problems the recruiter did not ask for.
ONLY when there is NO "Recruiter's Requirements" section do you derive problems from the JD (steps below).

# Company & role context (role-tuned, generated at JD time)

Ground the assignment's framing, scenarios, and what a strong submission looks like in THIS context. Let the role's own context define what good work means here — do not impose a generic engineering lens.
{company_context_json}

# Role-specific evaluation criteria

These are the dimensions this role is actually evaluated on (each with a label, weight, what_good_looks_like, anti_signals). The assignment's rubric MUST be derived from these — see the Rubric section below.
{evaluation_spec_json}

# IF NO RECRUITER REQUIREMENTS: READ THE JD FIRST

(Skip this section entirely if a "Recruiter's Requirements" section was given above — in that case the problems come from the recruiter, and the JD is only framing.)

The JD below contains the ACTUAL skills, tools, domain, and challenges for this role. Your assignment problems MUST test what the JD asks for. Do NOT default to generic engineering problems.

STEP 1 — Extract from the JD:
  * Primary function: engineering / product / design / marketing / sales / ops / finance / HR / legal / support
  * Domain: what business area does this role operate in?
  * Key tools/technologies mentioned
  * Key outcomes/responsibilities mentioned
  * Seniority level

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
  * Are the problems DIFFERENT in what they test? If any two overlap, replace one.

# Inputs

This brief is generated at the ROLE level, before any candidate applies. There is NO candidate data. Base everything on the JD and role below; never reference a specific candidate, resume, or prior answers.

Role: {role_title}

Job Description (THIS IS YOUR PRIMARY INPUT — base ALL problems on this):
{jd_text}

Constraints:
  * Total candidate time budget: {time_budget_hours} hours across ALL problems combined; candidate picks ONE.
  * Submission deadline: {deadline_days} days from receipt.
  * Problem count: {problem_count} — UNLESS the recruiter's requirements specify how many (then use THAT count exactly). Each problem is HARD and INDEPENDENT. Candidate selects whichever matches their strengths.

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
  * Multiple problems that test the same skill set.
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

# Per-problem fields ({problem_count} problems)

For EACH problem produce ALL of:
  * id: "p1", "p2", ... numbered sequentially up to the problem count.
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

evaluation_rubric.criteria: an ARRAY of OBJECTS, one per dimension in the "Role-specific evaluation criteria" JSON above. DERIVE the rubric from that array — do NOT invent your own dimension set, count, or weights.

For EACH dimension in {evaluation_spec_json}, produce one criterion object with exactly these keys:
  * name: the dimension's `label`, verbatim.
  * weight: the dimension's `weight`, unchanged (weights come from the spec — they need NOT be 5 in number nor 20 each; preserve whatever the spec gives).
  * description: 1 sentence on what a strong submission on THIS dimension looks like, grounded in the dimension's what_good_looks_like / anti_signals and the company context above.
Do NOT output bare strings. Each criterion is an object, e.g.:
  {"name": "Technical Depth", "weight": 20, "description": "Handles edge cases and scale, not just the happy path."}

If {evaluation_spec_json} is empty (`[]`), fall back to deriving 3-5 dimensions directly from the JD's most important requirements, with integer weights summing to 100.

# brief_md

brief_md: short markdown summary (~200 words). NOT a full re-rendering. Preview for dashboard textarea.

# Submission format

submission_format.type: "github_repo" for technical, "zip_upload" for non-technical.
submission_format.instructions: 2-3 sentences on how to submit.
submission_format.deadline_days: {deadline_days}.

Now produce strict JSON only. No commentary. No markdown fences."""
