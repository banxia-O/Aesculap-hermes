"""Aesculap — self-healing plugin for Hermes agents.

Core safety principle (PRD §1): *the LLM proposes, the code decides.* The LLM
only diagnoses and suggests; whether and how far to act is adjudicated by
deterministic code that the model can never override.
"""

__version__ = "0.1.0"
