"""Positions chosen on the page: a custom requirement, or one from an approved tender.

A custom requirement has the same shape as a data/tender.json position, so the checker uses it unchanged.
"""

import json
from typing import Any

from app import cli, finder, parse_tender

CUSTOM_KEY = "custom"
APPROVED_PREFIX = "approved:"
MAX_NAME_LENGTH = 80
MAX_CUSTOM_JSON = 4000
RequirementError = finder.FinderInputError


def _role_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise RequirementError("Please enter a role name.")
    name = " ".join(raw.split())
    if not name:
        raise RequirementError("Please enter a role name.")
    if len(name) > MAX_NAME_LENGTH:
        raise RequirementError(f"The role name must be at most {MAX_NAME_LENGTH} characters.")
    if not name.isprintable() or not any(character.isalnum() for character in name):
        raise RequirementError("The role name needs readable words.")
    return name


def _titles(raw: Any) -> list[str]:
    if isinstance(raw, list):
        if not all(isinstance(item, str) for item in raw):
            raise RequirementError("Similar-role titles must be text.")
        raw = ",".join(raw)
    return finder._word_list(raw, "Similar-role titles")


def validate_inline(raw: Any) -> dict[str, Any]:
    """Return a position with the data/tender.json shape, or raise a message safe to show."""
    if not isinstance(raw, dict):
        raise RequirementError("The custom requirement is missing.")
    name = _role_name(raw.get("name"))
    minimum_total = finder._years_input(raw.get("min_total_years"), "Minimum total years")
    minimum_similar = finder._years_input(raw.get("min_similar_years"), "Minimum years in similar roles")
    titles = _titles(raw.get("similar_title_keywords"))
    if minimum_similar > 0 and not titles:
        raise RequirementError("Please list the similar-role titles, because a minimum is set for them.")
    postgraduate = raw.get("postgraduate", "none")
    if postgraduate not in cli.POSTGRADUATE_LEVELS:
        raise RequirementError("Postgraduate must be none, preferential or required.")
    return {
        "name": name,
        "min_total_years": minimum_total,
        "min_similar_years": minimum_similar,
        "similar_title_keywords": titles,
        "postgraduate": postgraduate,
        "postgraduate_marks": None,
        "equivalent_grades": False,
    }


def parse_custom(text: Any) -> dict[str, Any] | None:
    """The custom requirement from a multipart form field, which carries it as JSON."""
    if not text:
        return None
    if not isinstance(text, str) or len(text) > MAX_CUSTOM_JSON:
        raise RequirementError("The custom requirement could not be read.")
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise RequirementError("The custom requirement could not be read.") from exc
    if not isinstance(value, dict):
        raise RequirementError("The custom requirement could not be read.")
    return value


def _approved_positions() -> dict[str, Any]:
    if not parse_tender.APPROVED_PATH.exists():
        return {}
    try:
        return cli._load_approved_tender(parse_tender.APPROVED_PATH)["positions"]
    except cli.InputError:
        return {}


def approved_options() -> list[dict[str, str]]:
    return [
        {"key": f"{APPROVED_PREFIX}{key}", "name": position.get("name", key)}
        for key, position in _approved_positions().items()
    ]


def unique_key(name: str, used: set[str]) -> str:
    key, count = name, 2
    while key in used:
        key, count = f"{name} ({count})", count + 1
    return key


def resolve(value: str, custom: Any, used: set[str]) -> tuple[str | None, dict[str, Any] | None]:
    """(position key, inline position). A plain tender key comes back with no inline position."""
    if value == CUSTOM_KEY:
        position = validate_inline(custom)
        return unique_key(position["name"], used), position
    if value.startswith(APPROVED_PREFIX):
        position = _approved_positions().get(value.removeprefix(APPROVED_PREFIX))
        if position is None:
            raise RequirementError("That approved tender position is no longer available.")
        return value, position
    return value or None, None
