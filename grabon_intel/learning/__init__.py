"""Online learning. Trains a logistic head (meeting probability) and a
regression head (close value INR) on top of the LLM rubric breakdown.

Pulls labels from `grabon_feedback`, joins with the latest dossier's
score breakdown, fits scikit-learn models, persists coefficients to
`score_weights`. Weekly Temporal cron schedules the workflow.
"""
from .retrain import RetrainResult, retrain

__all__ = ["retrain", "RetrainResult"]
