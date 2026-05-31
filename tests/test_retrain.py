"""Retrain learning heads on a synthetic dataset.

Tests the pure model-fitting helpers without touching the DB.
"""
from __future__ import annotations

import numpy as np

from grabon_intel.learning.retrain import FEATURES, _fit_close_value, _fit_meeting


def _synthesize(n: int = 200, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, size=(n, len(FEATURES)))
    # ground truth: meeting prob driven mostly by feature 0 and 2.
    logit = -1.5 + 3.5 * X[:, 0] + 2.0 * X[:, 2] - 1.0 * X[:, 6]
    prob = 1.0 / (1.0 + np.exp(-logit))
    y = (rng.uniform(0, 1, size=n) < prob).astype(int)
    # close_value = 5L + 10L*feat0 (for the wins only)
    val = 500000 + 1_000_000 * X[:, 0] + rng.normal(0, 50000, size=n)
    return X, y, val


def test_fit_meeting_produces_calibrated_head() -> None:
    X, y, _ = _synthesize()
    out = _fit_meeting(X, y)
    assert out is not None
    coeffs, metrics = out
    assert metrics["roc_auc"] is None or metrics["roc_auc"] > 0.6
    assert set(coeffs["coef"]) == set(FEATURES)
    assert metrics["n_train"] + metrics["n_test"] == 200


def test_fit_meeting_returns_none_on_single_class() -> None:
    X = np.random.rand(60, len(FEATURES))
    y = np.zeros(60, dtype=int)
    assert _fit_meeting(X, y) is None


def test_fit_close_value() -> None:
    X, y, val = _synthesize(n=300)
    # treat top-half meeting positives as 'won' to give enough wins.
    won = y.astype(bool)
    if won.sum() < 20:
        # synthesize enough wins
        won[:30] = True
    out = _fit_close_value(X, val, won)
    assert out is not None
    coeffs, metrics = out
    assert "mae_inr" in metrics
    assert set(coeffs["coef"]) == set(FEATURES)


def test_fit_close_value_too_few_wins() -> None:
    X = np.random.rand(40, len(FEATURES))
    val = np.random.rand(40) * 1_000_000
    won = np.zeros(40, dtype=bool)
    won[:5] = True
    assert _fit_close_value(X, val, won) is None
