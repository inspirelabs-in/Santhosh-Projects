ALL_ENGINES = ["google_aio", "google_ai_mode", "perplexity", "gemini", "chatgpt", "claude"]
VISIBLE_ENGINES = ALL_ENGINES
AUTH_ENGINES = {"chatgpt", "claude", "gemini", "perplexity"}
UNAUTH_ENGINES = {"google_aio", "google_ai_mode", "google_serp"}

ENGINE_LABELS = {
    "google_aio": "AI Overview",
    "google_ai_mode": "AI Mode",
    "perplexity": "Perplexity",
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
}

ENGINE_COLORS = {
    "google_aio": "#4285F4",
    "google_ai_mode": "#34A853",
    "perplexity": "#20B8CD",
    "gemini": "#8E75B2",
    "chatgpt": "#10A37F",
    "claude": "#D97706",
}
