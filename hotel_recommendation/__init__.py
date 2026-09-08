"""Deterministic hotel recommendation core for Vertical Slice 0."""

from .config import SCORING_VERSION
from .pipeline import run_vertical_slice

__all__ = ["SCORING_VERSION", "run_vertical_slice"]
