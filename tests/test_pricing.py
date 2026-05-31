from grabon_intel.llm.pricing import cost_cents, price_for


def test_nvidia_free_tier_zero() -> None:
    assert cost_cents("nvidia/llama-3.3-70b", 100_000, 50_000) == 0


def test_sonnet_pricing_rounds_up() -> None:
    # 1M in @ $3 + 1M out @ $15 = $18 = 1800c
    assert cost_cents("anthropic/claude-sonnet-4-6", 1_000_000, 1_000_000) == 1800


def test_haiku_small_call_costs_at_least_one_cent() -> None:
    # any non-zero usage on a paid model bills at least 1c (round up).
    assert cost_cents("anthropic/claude-haiku-4-5", 100, 100) >= 1


def test_cache_read_cheaper_than_fresh_input() -> None:
    fresh = cost_cents("anthropic/claude-sonnet-4-6", 200_000, 0)
    cached = cost_cents("anthropic/claude-sonnet-4-6", 200_000, 0, cache_read_tokens=200_000)
    assert cached < fresh


def test_unknown_model_uses_pessimistic_default() -> None:
    p = price_for("mystery/unknown-model")
    assert p.input_per_million_usd > 0 and p.output_per_million_usd > 0


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("GRABON_LLM_PRICE_OPENAI_GPT_4O_MINI_IN", "0.30")
    p = price_for("openai/gpt-4o-mini")
    assert p.input_per_million_usd == 0.30
