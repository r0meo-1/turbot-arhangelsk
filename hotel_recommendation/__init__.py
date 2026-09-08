"""Deterministic hotel recommendation core for Vertical Slice 0."""

from .pipeline import SCORING_VERSION, run_vertical_slice

__all__ = ["SCORING_VERSION", "run_vertical_slice"]
