"""Py AI — built-in read-only assistant (standalone feature). See docs/PLAN_pyai.md."""

from .runtime import PyAIError, PyAIResult, PyAIService

__all__ = ["PyAIService", "PyAIResult", "PyAIError"]
