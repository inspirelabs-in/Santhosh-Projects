"""PARSE_RESUME_V2 -- Stage 3 structured resume extraction (comprehensive)."""

PARSE_RESUME_VERSION = "v2"

PARSE_RESUME_V1 = """You are a precise resume parser. Extract EVERYTHING present in this resume into the structured schema below. Resumes follow wildly different templates: one person lists Projects, another lists Publications, a third lists Hackathons. Capture whatever is there; leave unstated fields null or empty.

General rules:
- Extract exactly what is stated. Do not invent, infer, or merge information that is not written.
- Normalise phone numbers to E.164 (+91XXXXXXXXXX for Indian numbers).
- For URLs, keep the full href if present; strip tracking params.
- Trim surrounding whitespace, bullet glyphs, and boilerplate ("•", "—").
- Skills: technical / professional tools, languages, frameworks. Keep them atomic (e.g. "PostgreSQL" not "PostgreSQL database").
- Tools: IDEs, platforms, cloud services, SaaS used (Figma, AWS, Jira, Kubernetes). Do not duplicate items already in `skills`.
- Soft skills: leadership, communication, stakeholder management, etc. (only if explicitly listed).
- Domains: industry verticals the candidate has worked in (fintech, healthcare, ecommerce, gaming, defence).
- Projects: any section titled Projects / Side Projects / Academic Projects / Case Studies. Capture description, tech stack, role, url, and bullet highlights.
- Achievements: awards, hackathon wins, scholarships, dean's list, rankings, recognitions.
- Publications: papers, articles, blog posts, talks (with venue + year + url if present).
- Patents: patent titles or patent numbers.
- Open source: repo urls or "org/name" strings from an OSS / Contributions section.
- Certifications: course / exam certificates (AWS SAA, GCP ACE, PMP, etc.).
- Languages: spoken languages with proficiency if stated.
- Volunteer experience: NGO work, community involvement.
- Extracurriculars: clubs, sports, student-org leadership.
- Availability / notice period: capture both `notice_period_days` (numeric) and `availability` (free-text if descriptive).
- CTC: from "CTC", "salary", "compensation", "package", "LPA", "per annum". Null if not stated.
- Experience: `total_years_experience` as a decimal (e.g. 3.5). Null if resume doesn't state it explicitly — do NOT sum dates yourself.
- Headline: a one-line professional tagline (e.g. "Senior Backend Engineer · Go / Postgres").
- Summary: the longer "About me" / Profile / Objective paragraph if present.
- Rate confidence (0.0-1.0) for the listed critical fields. Lower your confidence when the resume is scanned/OCR-like or fields are ambiguous.
- If a field is not present in the resume, return null (scalar) or [] (list). Do not fabricate placeholder text.

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
    {{
      "company": "string",
      "role": "string",
      "duration": "string",
      "highlights": ["string"]
    }}
  ],
  "projects": [
    {{
      "name": "string",
      "description": "string or null",
      "role": "string or null",
      "tech_stack": ["string"],
      "url": "string or null",
      "duration": "string or null",
      "highlights": ["string"]
    }}
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
