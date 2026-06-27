"""JOURNEY_REPORT_V1 -- synthesize full applicant journey into HR-facing report."""



JOURNEY_REPORT_VERSION = "v7"  # flight-risk thresholds dynamic: {ctc_overshoot_pct} + {max_notice_days}
JOURNEY_REPORT_V1 = """You are preparing a one-page applicant journey report for the HR team.



## Company & role context (role-tuned, generated at JD time)

Ground cultural alignment assessment in THIS context.

{company_context_json}



## Role-specific evaluation criteria

{evaluation_spec_json}



Role: {role_title}

Candidate: {candidate_name} ({candidate_email})



Timeline Data:

- Application received: {applied_at}

- Source: {source_channel}

- Resume-parsed profile summary: {profile_summary}

- Screening questions asked: {screening_questions_json}

- Candidate's screening answers (verbatim): {screening_responses_json}

- Screening evaluation (scores, verdict, logistics): {screening_evaluation_json}

- Logistics extracted from screening answers: {logistics_values_json}

- Assignment submission summary: {assignment_summary_json}

- Current stage: {current_stage}



Write a clean, professional report in plain Markdown.



Formatting:

- Use `## ` for section headings. Use `- ` for bullets. No other markdown.

- Stick to plain text: no bold, italic, backticks, blockquotes, tables, or emoji.

- Write labels as plain text, e.g. `Current role: UI/UX Designer at AAGL` rather than using asterisks.

- Keep each bullet to one short sentence with no sub-bullets.

- When a value is missing in the data, omit the bullet entirely rather than writing "Not specified", "N/A", or "Unknown".



Sections (exactly these, in this order):



## Quick Verdict

One sentence: HIRE / LEAN HIRE / HOLD / LEAN REJECT / REJECT, with the single most important reason.



## Summary

2-3 sentences: who this candidate is, where they are in the pipeline, what makes them interesting or risky.



## Profile Snapshot

- Current role: <title at company>

- Total experience: <N years>

- Top 3 skills relevant to the role: <comma-separated>

- Current CTC: <X LPA>

- Expected CTC: <Y LPA>

- Notice period: <N days>

- Location: <city, country>



For CTC, notice, and location, prefer values from "Logistics extracted from screening answers" (logistics_values_json). Fall back to the resume-parsed profile only if the screening value is null/missing. Omit the line if neither has it.



## Screening Highlights

- Strengths: 1-2 bullets citing what the candidate answered well (quote or paraphrase the specific answer).

- Concerns: 1-2 bullets on gaps. If none, write `- No concerns identified.`

- Verdict: {screening_verdict}



## Assignment Review

- Completeness and quality: 1-3 bullets.

- Standout items: 1-2 bullets if any.

- Concerns: 1-2 bullets if any.



## Cultural Alignment

Assess against the values and traits from the company context and evaluation criteria above.

- Overall fit: Strong / Moderate / Weak, with one supporting observation from the data.



## Risk Assessment

- Resume-screening consistency: Do screening answers match resume claims? Flag any contradictions.

- AI-generation suspicion: If screening answers lack personal specifics across 3+ questions, note it.

- Flight risk: If expected CTC is more than {ctc_overshoot_pct}% above the role's range, or notice period exceeds {max_notice_days} days, flag.



## Recommendation for HR

- Next step: advance / hold for review / reject (one line).

- Follow-up questions HR should ask: 2-3 bullets specific to this candidate's gaps.

- Compliance or logistics flags: any CTC/notice/location mismatch vs. role requirements, or "None" if clean.



Keep under 500 words. Cite facts from the data — the report's credibility rests on accuracy.

"""

