"""Golden brand set loader.

YAML schema:

```yaml
- name: Mamaearth
  domain: mamaearth.in
  expected:
    category_contains: ["beauty", "personal care"]
    services_recommended_any: [performance_marketing, social_media, influencer]
    tier_in: [hot, warm]
    score_min: 60
```
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml


@dataclasses.dataclass(slots=True)
class GoldenCase:
    name: str
    domain: str | None
    expected: dict[str, Any]


def load_golden(path: str | Path) -> list[GoldenCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    out: list[GoldenCase] = []
    for item in raw:
        out.append(
            GoldenCase(
                name=str(item.get("name") or "?"),
                domain=item.get("domain"),
                expected=dict(item.get("expected") or {}),
            )
        )
    return out
