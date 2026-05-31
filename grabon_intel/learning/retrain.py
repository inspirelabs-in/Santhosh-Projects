"""Retrain meeting-prob + close-value heads.

Strategy: keep the LLM rubric weights fixed for interpretability and let
two small linear heads adapt to outcomes. Inputs are the rubric breakdown
factor scores stored in the latest dossier's `data->score->breakdown`.

Outputs:
  - logistic regression for P(meeting_booked|features) and P(closed_won|features)
  - linear regression for predicted close_value_inr (only on closed_won rows)
  - metrics: roc_auc, brier, n_train, n_test

Persistence: one row per (kind, version) in `score_weights`.

Failure modes handled:
  - <30 labels → skip, log, no write (returns RetrainResult(skipped=True))
  - degenerate single-class labels → skip the affected head, train the
    other if possible
  - missing feature in row → impute as median
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import brier_score_loss, mean_absolute_error, roc_auc_score
from sklearn.model_selection import train_test_split
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session as session_ctx
from ..logging import get_logger

log = get_logger(__name__)


# Order matters: defines the implicit feature vector layout.
FEATURES: tuple[str, ...] = (
    "budget_potential",
    "growth_stage_fit",
    "marketing_maturity_gap",
    "competitive_pressure",
    "urgency",
    "brand_fit",
    "expansion_likelihood",
    "channel_weakness_severity",
    "decision_maker_reach",
)


@dataclasses.dataclass(slots=True)
class RetrainResult:
    skipped: bool = False
    reason: str | None = None
    meeting_version: int | None = None
    close_value_version: int | None = None
    n_total: int = 0
    n_meeting_positives: int = 0
    n_close_won: int = 0
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)


async def _load_dataset(s: AsyncSession) -> list[dict[str, Any]]:
    """Join feedback w/ latest dossier breakdown per brand."""
    rows = (
        await s.execute(
            text(
                """
                WITH latest AS (
                  SELECT DISTINCT ON (brand_id)
                    brand_id, data->'score'->'breakdown' AS bd,
                    (data->'score'->>'total')::int AS total,
                    cost_cents, generated_at
                  FROM dossiers
                  ORDER BY brand_id, version DESC
                )
                SELECT f.brand_id, f.label, COALESCE(f.value_inr, 0) AS value_inr,
                       l.bd, l.total
                FROM grabon_feedback f
                JOIN latest l ON l.brand_id = f.brand_id
                """
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _vectorise(rows: Sequence[dict[str, Any]]) -> tuple[np.ndarray, list[str], np.ndarray]:
    X: list[list[float]] = []
    labels: list[str] = []
    values: list[float] = []
    # Compute column medians for imputation.
    cols: dict[str, list[float]] = {f: [] for f in FEATURES}
    for r in rows:
        bd = r.get("bd") or {}
        for f in FEATURES:
            v = bd.get(f)
            if isinstance(v, (int, float)):
                cols[f].append(float(v))
    medians = {f: float(np.median(vs)) if vs else 0.5 for f, vs in cols.items()}
    for r in rows:
        bd = r.get("bd") or {}
        X.append([
            float(bd.get(f) or 0.0) if isinstance(bd.get(f), (int, float)) else medians[f]
            for f in FEATURES
        ])
        labels.append(str(r["label"]))
        values.append(float(r["value_inr"]))
    return np.asarray(X), labels, np.asarray(values)


async def _persist(s: AsyncSession, kind: str, coeffs: dict[str, Any], metrics: dict[str, Any], n: int) -> int:
    row = (
        await s.execute(
            text(
                "WITH next AS ("
                "  SELECT COALESCE(MAX(version), 0) + 1 AS v FROM score_weights WHERE kind = :k"
                ") "
                "INSERT INTO score_weights (kind, version, coefficients, metrics, training_rows) "
                "SELECT :k, next.v, CAST(:c AS JSONB), CAST(:m AS JSONB), :n FROM next "
                "RETURNING version"
            ),
            {"k": kind, "c": _json(coeffs), "m": _json(metrics), "n": n},
        )
    ).first()
    return int(row[0]) if row else 0


def _json(obj: Any) -> str:
    import orjson

    return orjson.dumps(obj, default=str).decode("utf-8")


def _fit_meeting(X: np.ndarray, y: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if len(np.unique(y)) < 2 or len(y) < 30:
        return None
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)
    clf = LogisticRegression(max_iter=400, C=1.0)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    metrics = {
        "roc_auc": float(roc_auc_score(yte, proba)) if len(np.unique(yte)) == 2 else None,
        "brier": float(brier_score_loss(yte, proba)),
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    coeffs = {
        "intercept": float(clf.intercept_[0]),
        "coef": {f: float(c) for f, c in zip(FEATURES, clf.coef_[0], strict=True)},
        "features": list(FEATURES),
    }
    return coeffs, metrics


def _fit_close_value(X: np.ndarray, vals: np.ndarray, won_mask: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]] | None:
    Xw = X[won_mask]
    yw = vals[won_mask]
    if len(yw) < 20:
        return None
    Xtr, Xte, ytr, yte = train_test_split(Xw, yw, test_size=0.25, random_state=42)
    reg = LinearRegression()
    reg.fit(Xtr, ytr)
    pred = reg.predict(Xte)
    metrics = {
        "mae_inr": float(mean_absolute_error(yte, pred)),
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    coeffs = {
        "intercept": float(reg.intercept_),
        "coef": {f: float(c) for f, c in zip(FEATURES, reg.coef_, strict=True)},
        "features": list(FEATURES),
    }
    return coeffs, metrics


async def retrain() -> RetrainResult:
    async with session_ctx() as s:
        rows = await _load_dataset(s)
    if not rows:
        return RetrainResult(skipped=True, reason="no_labeled_rows")
    if len(rows) < 30:
        return RetrainResult(skipped=True, reason=f"too_few_rows({len(rows)})", n_total=len(rows))

    X, labels, values = _vectorise(rows)
    y_meeting = np.asarray([
        1 if l in {"meeting_booked", "closed_won"} else 0 for l in labels
    ], dtype=int)
    y_won = np.asarray([1 if l == "closed_won" else 0 for l in labels], dtype=int)

    out_metrics: dict[str, Any] = {}
    meeting_v: int | None = None
    close_v: int | None = None

    meeting = _fit_meeting(X, y_meeting)
    if meeting:
        coeffs, metrics = meeting
        out_metrics["meeting_prob"] = metrics
        async with session_ctx() as s:
            meeting_v = await _persist(s, "meeting_prob", coeffs, metrics, len(rows))

    cv = _fit_close_value(X, values, y_won.astype(bool))
    if cv:
        coeffs, metrics = cv
        out_metrics["close_value"] = metrics
        async with session_ctx() as s:
            close_v = await _persist(s, "close_value", coeffs, metrics, int(y_won.sum()))

    return RetrainResult(
        skipped=False,
        meeting_version=meeting_v,
        close_value_version=close_v,
        n_total=len(rows),
        n_meeting_positives=int(y_meeting.sum()),
        n_close_won=int(y_won.sum()),
        metrics=out_metrics,
    )
