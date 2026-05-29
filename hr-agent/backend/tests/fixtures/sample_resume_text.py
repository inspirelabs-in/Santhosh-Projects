"""Sample resume text + parsed profile payloads for tests."""

SAMPLE_RESUME_TEXT = """ALICE KUMAR
Senior Backend Engineer
alice@example.com | +91 98765 43210 | linkedin.com/in/alice-kumar | Hyderabad, India

EXPERIENCE
Flipkart (2021 - Present) — Senior SDE
- Led migration of payments microservice from monolith to Go, reducing P99 latency by 40%.
- Owned on-call rotation for checkout flow (~6M req/day).

Razorpay (2018 - 2021) — SDE II
- Built webhook delivery system handling 200K events/hour.

EDUCATION
B.Tech, Computer Science -- IIIT Hyderabad, 2018

SKILLS
Python, Go, PostgreSQL, Redis, Kubernetes, gRPC, Kafka

COMPENSATION
Current CTC: 38 LPA. Expected: 50 LPA. Notice period: 60 days.
"""


SAMPLE_PARSED_PROFILE_JSON = {
    "name": "Alice Kumar",
    "email": "alice@example.com",
    "phone": "+919876543210",
    "linkedin_url": "https://www.linkedin.com/in/alice-kumar",
    "github_url": None,
    "portfolio_url": None,
    "location": "Hyderabad, India",
    "current_role": "Senior SDE",
    "current_company": "Flipkart",
    "current_ctc_lpa": 38.0,
    "expected_ctc_lpa": 50.0,
    "notice_period_days": 60,
    "total_years_experience": 6.0,
    "education": [
        {"degree": "B.Tech, Computer Science", "institution": "IIIT Hyderabad", "year": 2018}
    ],
    "skills": ["Python", "Go", "PostgreSQL", "Redis", "Kubernetes", "gRPC", "Kafka"],
    "work_history": [
        {
            "company": "Flipkart",
            "role": "Senior SDE",
            "duration": "2021 - Present",
            "highlights": [
                "Led migration of payments microservice from monolith to Go, reducing P99 latency by 40%.",
                "Owned on-call rotation for checkout flow (~6M req/day).",
            ],
        },
        {
            "company": "Razorpay",
            "role": "SDE II",
            "duration": "2018 - 2021",
            "highlights": ["Built webhook delivery system handling 200K events/hour."],
        },
    ],
    "certifications": [],
    "field_confidence": {
        "name": 0.98,
        "email": 0.99,
        "phone": 0.95,
        "current_ctc_lpa": 0.9,
        "notice_period_days": 0.9,
        "total_years_experience": 0.85,
        "skills": 0.9,
    },
}
