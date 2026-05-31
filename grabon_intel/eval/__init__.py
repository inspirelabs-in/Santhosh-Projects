"""Eval harness — runs the supervisor over a golden brand set and scores
the result against expectations. Optional Langfuse hook wraps every LLM
call when keys are configured.
"""
from .golden import GoldenCase, load_golden
from .runner import EvalRecord, EvalReport, run_eval

__all__ = ["GoldenCase", "load_golden", "EvalRecord", "EvalReport", "run_eval"]
