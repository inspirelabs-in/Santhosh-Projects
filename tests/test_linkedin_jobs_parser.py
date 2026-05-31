"""LinkedIn Jobs HTML card parser — covers happy path + dedup."""
from __future__ import annotations

from grabon_intel.signals.linkedin_jobs import _parse_cards


_SAMPLE = """
<li>
  <div class="base-card">
    <h3 class="base-search-card__title">Performance Marketing Manager</h3>
    <h4 class="base-search-card__subtitle"><a>Mamaearth</a></h4>
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/abc"></a>
    <time datetime="2026-05-10">3 days ago</time>
  </div>
</li>
<li>
  <div class="base-card">
    <h3 class="base-search-card__title">SEO Specialist</h3>
    <h4 class="base-search-card__subtitle"><a>boAt Lifestyle</a></h4>
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/xyz"></a>
    <time>1 week ago</time>
  </div>
</li>
<li>
  <div class="base-card">
    <h3 class="base-search-card__title">Performance Marketing Manager</h3>
    <h4 class="base-search-card__subtitle"><a>Mamaearth</a></h4>
  </div>
</li>
"""


def test_parses_titles_and_companies() -> None:
    cards = _parse_cards(_SAMPLE)
    companies = sorted({c["company"] for c in cards})
    assert "Mamaearth" in companies
    assert "boAt Lifestyle" in companies


def test_dedup_within_page() -> None:
    cards = _parse_cards(_SAMPLE)
    # Mamaearth/Performance Marketing Manager appears twice in input — should dedup.
    pairs = [(c["title"], c["company"]) for c in cards]
    assert pairs.count(("Performance Marketing Manager", "Mamaearth")) == 1


def test_handles_empty() -> None:
    assert _parse_cards("") == []
