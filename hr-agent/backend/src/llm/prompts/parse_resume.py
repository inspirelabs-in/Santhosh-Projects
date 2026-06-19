"""PARSE_RESUME_V3 -- Structured resume extraction. No vision — text only."""

PARSE_RESUME_VERSION = "v3"

PARSE_RESUME_V1 = """You are a precise resume parser. Extract EVERYTHING present in this resume into the schema below. Resumes follow wildly different templates; capture whatever is there.

# TEXT EXTRACTION RULES

## Contact fields (email, phone, URLs)
- Email: extract verbatim, lower-case, trim whitespace. No fabricating.
- Phone: normalise to E.164 (+91XXXXXXXXXX for Indian numbers).
- **URLs are the link, not the label.** When a field says `github_url`, `linkedin_url`, or `portfolio_url` you MUST extract the raw URL (e.g. `https://github.com/jyothi-kumar`). NEVER extract the label ("GitHub", "GitHub Profile", "Portfolio", "LinkedIn"). If the URL has no protocol prefix (e.g. `github.com/user`), return it as-is.

## Work & experience
- `total_years_experience`: decimal number. Include ALL professional work (internships, full-time roles). Do NOT count college internships, academic projects, or teaching-assistant work done during college. If the resume doesn't explicitly state total years, infer it from the date ranges across work history entries — do NOT sum them manually.
- `current_role` / `current_company`: from the most recent work entry.
- `work_history`: every role listed. Capture company, role title, duration, and bullet-point highlights.
- CTC: from "CTC", "salary", "package", "LPA", "per annum". Null if not stated.

## Skills & classifications
- `skills`: technical languages, frameworks, databases. Atomic values ("PostgreSQL" not "PostgreSQL database").
- `tools`: IDEs, platforms, SaaS (Figma, AWS, Jira, Docker). Do NOT duplicate `skills`.
- `soft_skills`: leadership, communication, stakeholder management — only if explicitly listed.
- `domains`: industry verticals (fintech, healthcare, ecommerce, gaming, defence).

## Projects, achievements & extras
- `projects`: any section titled Projects / Side Projects / Academic Projects / Case Studies. Capture description, tech stack, role, url, highlights.
- `achievements`: awards, hackathon wins, scholarships, rankings, recognitions.
- `publications`: papers, articles, blog posts, talks. Include venue, year, URL if present.
- `open_source`: repo URLs or "org/name" strings from an OSS / Contributions section.
- `certifications`: exam certificates (AWS SAA, GCP ACE, PMP, etc.).
- `languages`: spoken languages with proficiency if stated.
- `volunteer_experience`, `extracurriculars`: club involvement, NGO work, community.

## Formatting & confidence
- Trim whitespace, bullet glyphs, boilerplate ("•", "—").
- Null/empty: return null for scalars, [] for lists. Do NOT fabricate.
- `field_confidence`: rate 0.0-1.0 for each critical field. Lower confidence for OCR-scanned or ambiguous content.

Resume text:
{resume_text}

Respond in this EXACT JSON format (no prose, no markdown):
{{
  "name": "string or null",
  "email": "string or null",
  "phone": "string in E.164 or null",
  "linkedin_url": "string or null",
  "github_url": "string or null",
  "portfolio_url": "string or null",
  "location": "city, state or null",
  "current_role": "string or null",
  "current_company": "string or null",
  "current_ctc_lpa": number or null,
  "expected_ctc_lpa": number or null,
  "notice_period_days": number or null,
  "availability": "string or null",
  "preferred_work_mode": "onsite|hybrid|remote or null",
  "willing_to_relocate": true/false/null,
  "references_available": true/false/null,
  "total_years_experience": number or null,
  "headline": "string or null",
  "summary": "string or null",
  "education": [
    {{"degree": "string", "institution": "string", "year": number or null}}
  ],
  "skills": ["string"],
  "tools": ["string"],
  "soft_skills": ["string"],
  "domains": ["string"],
  "work_history": [
    {{"company": "string", "role": "string", "duration": "string", "highlights": ["string"]}}
  ],
  "projects": [
    {{"name": "string", "description": "string or null", "role": "string or null", "tech_stack": ["string"], "url": "string or null", "duration": "string or null", "highlights": ["string"]}}
  ],
  "achievements": [
    {{"title": "string", "issuer": "string or null", "year": number or null, "description": "string or null"}}
  ],
  "publications": [
    {{"title": "string", "venue": "string or null", "year": number or null, "url": "string or null"}}
  ],
  "patents": ["string"],
  "open_source": ["string"],
  "certifications": ["string"],
  "languages": [
    {{"language": "string", "proficiency": "native|fluent|intermediate|basic or null"}}
  ],
  "volunteer_experience": ["string"],
  "extracurriculars": ["string"],
  "field_confidence": {{
    "name": 0.0-1.0,
    "email": 0.0-1.0,
    "phone": 0.0-1.0,
    "current_ctc_lpa": 0.0-1.0,
    "notice_period_days": 0.0-1.0,
    "total_years_experience": 0.0-1.0,
    "skills": 0.0-1.0
  }}
}}"""
