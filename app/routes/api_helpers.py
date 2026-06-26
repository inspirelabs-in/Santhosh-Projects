import uuid as _uuid


def valid_uuid(val: str) -> bool:
    try:
        _uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False


def classify_error(raw: str) -> tuple[str, str, str]:
    """Classify error from raw_response_text.

    Returns (status, error_type, error_short) for display.
    """
    if not raw:
        return "pending", "", ""
    low = raw.lower()

    if raw.startswith("[No AI Overview]"):
        return "no_aio", "no_aio", "No AI Overview"
    if raw.startswith("[No AI Mode]"):
        return "no_aio", "no_ai_mode", "No AI Mode"

    if "limit_exhausted" in low or ("limit" in low and ("rate" in low or "message" in low or "free" in low)):
        return "limit_exhausted", "rate_limit", "Rate limit hit"
    if "429" in raw or "too many requests" in low:
        return "limit_exhausted", "rate_limit", "429 Too Many Requests"

    if "captcha" in low or "unusual traffic" in low or "not a robot" in low:
        return "error", "captcha", "Captcha detected"
    if "captcha_penalty" in low:
        return "error", "captcha_penalty", "IP penalized (captcha)"

    if "timeout" in low or "timed out" in low or "timedout" in low:
        return "error", "timeout", "Scrape timed out"

    if "proxy" in low or "ssl" in low and "certificate" in low:
        return "error", "proxy", "Proxy/SSL error"
    if "connection" in low and ("refused" in low or "reset" in low or "closed" in low):
        return "error", "connection", "Connection failed"

    if "expired" in low or "session" in low and ("invalid" in low or "expired" in low):
        return "error", "auth", "Session expired"
    if "sign in" in low or "log in" in low or "login" in low:
        return "error", "auth", "Auth required"

    if "paa_content" in low or "people also ask" in low:
        return "error", "paa_content", "PAA leak (not AIO)"
    if "featured_snippet" in low:
        return "error", "featured_snippet", "Featured snippet"
    if "maps_content" in low:
        return "error", "maps_content", "Maps result"
    if "ui_noise" in low:
        return "error", "ui_noise", "UI noise only"

    if "empty" in low or "no response" in low or raw.strip() == "Error:":
        return "error", "empty", "Empty response"

    if raw.startswith("Error:"):
        reason = raw[6:].strip()[:80]
        return "error", "unknown", reason or "Unknown error"

    return "pending", "", ""
