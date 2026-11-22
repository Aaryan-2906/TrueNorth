"""Settings and thresholds, loaded from the environment and .env."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_TODAY = (2026, 11)
DATE_TOLERANCE_MONTHS = 3
CLAIM_GAP_MONTHS = 24


def read_today() -> tuple[int, int]:
    """Return (year, month) from env TODAY=YYYY-MM, or the default if unset."""
    value = os.getenv("TODAY", "").strip()
    if not value:
        return DEFAULT_TODAY
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", value)
    if match is None:
        raise ValueError(f"TODAY must be YYYY-MM, got {value!r}")
    return int(match.group(1)), int(match.group(2))


def read_fresh() -> bool:
    """Return True if env FRESH is set to a truthy value."""
    return os.getenv("FRESH", "").strip().lower() in {"1", "true", "yes", "on"}


def read_llm_enabled() -> bool:
    """The AI reader is on unless env LLM_ENABLED is set to a falsy value."""
    return os.getenv("LLM_ENABLED", "").strip().lower() not in {"0", "false", "no", "off"}


CRUSTDATA_API_KEY = os.getenv("CRUSTDATA_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL
FRESH = read_fresh()
LLM_ENABLED = read_llm_enabled()
TODAY = read_today()
