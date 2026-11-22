"""Reason code -> committee question templates. No LLM: the wording is fixed here."""

from dataclasses import dataclass

from app.models import Finding

TEMPLATES = {
    "CV_DATE_INVALID": (
        "Row {n} of the CV gives the date '{raw}', which {problem}. This may be a typing error. "
        "It was left out of all totals. Please ask the bidder to correct it."
    ),
    "CV_HEADER_BODY_MISMATCH": (
        "CV row {n} gives the {field} date as {header} in its heading and {body} in its Duration text. "
        "Please ask the bidder which date is correct."
    ),
    "CV_ROWS_OVERLAP": (
        "CV rows {n_a} ({employer_a}) and {n_b} ({employer_b}) overlap by {overlap} months. "
        "Please ask the bidder whether both roles were held at the same time."
    ),
    "CV_EMPLOYER_UNCLEAR": (
        "CV row {n} ({title}) does not make the employer clear, so it could not be compared with the profile. "
        "Please ask the bidder to name the employer."
    ),
    "CLAIM_EXCEEDS_CV_ROWS": (
        "The CV claims about {claimed} years, but its employment record lists {listed} years of jobs. "
        "Please ask the bidder to account for the difference."
    ),
    "ROW_NOT_ON_PROFILE": (
        "CV row {n} lists {employer}, which does not appear on the profile. Profiles can be incomplete. "
        "Please ask for an employment certificate."
    ),
    "ROW_MISSING_DATES": (
        "The profile lists {employer} without complete dates, so CV row {n} cannot be checked. "
        "Please ask for an employment certificate."
    ),
    "ROW_DATES_DIFFER": (
        "CV row {n} gives {cv_dates} at {employer}, but the profile shows {profile_dates}. "
        "Please ask for evidence of the employment dates."
    ),
    "ROW_TITLE_DIFFERS": (
        "CV row {n} gives the title '{cv}', but the profile shows '{profile}' at {employer} {period}. "
        "Please ask for the appointment letter."
    ),
    "NO_PROFILE_FOUND": (
        "No profile was available, so the CV could only be checked against itself. "
        "This is not evidence against the expert."
    ),
    "AMBIGUOUS_IDENTITY": (
        "{count} profiles match this expert and none was chosen, so the CV could only be checked against itself. "
        "This is not evidence against the expert. Please ask the bidder for the expert's exact profile link."
    ),
    "PROFILE_THIN": (
        "Only {dated} of the {total} jobs on the profile have a start date, so total experience cannot be checked. "
        "Please ask for employment certificates."
    ),
    "YEARS_SHORTFALL": (
        "The tender asks for at least {min} years. The expert's public profile documents {doc} years. "
        "Please ask the bidder for evidence of the remaining experience."
    ),
    "CLAIM_EXCEEDS_RECORD": (
        "The CV claims {claimed} years. The expert's public profile documents {doc} years. "
        "Please ask the bidder for evidence of the difference."
    ),
    "SIMILAR_ROLE_UNPROVEN": (
        "The tender asks for at least {min} years in a similar role. The expert's public profile documents "
        "{doc} years under titles counted as similar{claim_part}. The committee decides what counts as similar. "
        "Please ask the bidder for evidence of this experience."
    ),
    "CURRENT_ROLE_ELSEWHERE": (
        "The profile shows {roles} ({names}). "
        "Please confirm the expert's availability for the full assignment and their employment with the bidder."
    ),
    "OVERLAPPING_CURRENT_ROLES": (
        "The profile shows current roles at {n} different organisations ({names}). "
        "Please confirm the expert's availability for the full assignment."
    ),
    "EXPERT_NOT_AT_FIRM": (
        "The bid lists the expert as full-time with {bidder}, but the profile shows no current role there. "
        "Profiles can be out of date. Please ask for proof of employment."
    ),
    "EDUCATION_NOT_SHOWN": (
        "The tender asks for a postgraduate degree. {shown} Please ask for the degree certificate."
    ),
    "DUPLICATE_ENTRY": "The profile repeats one job ({kept} and {dropped}). It was counted once.",
    "IMPLAUSIBLE_PROFILE": (
        "The profile shows senior leadership roles at {n} organisations at the same time. Profile data can be "
        "unreliable. Please ask the bidder to confirm the expert's actual roles."
    ),
    "FIRM_NOT_FOUND": (
        "No company record was available for the bidding firm, so its headcount could not be checked. "
        "This is not evidence against the firm."
    ),
    "FIRM_MULTIPLE_RECORDS": (
        "Crustdata holds {count} records that may be the same firm: {records}. "
        "The largest range was used for the headcount check."
    ),
    "FIRM_HEADCOUNT_UNKNOWN": "No headcount or employee range is recorded for this firm, so it could not be compared.",
    "FIRM_HEADCOUNT_LOW": (
        "The company record shows {range} staff, but the bid lists {count} key experts as full-time. "
        "Company records can be out of date. Please ask the bidder for staff records."
    ),
}

DATE_PROBLEM_TEXT = {
    "after today": "is after today",
    "no year": "has no year",
    "end before start": "ends before it starts",
    "unreadable": "could not be read",
}

# These codes state a limit of the check; there is nothing to ask the bidder.
NO_ACTION_CODES = {"NO_PROFILE_FOUND", "FIRM_NOT_FOUND"}


@dataclass
class Question:
    number: int
    codes: list[str]
    text: str
    evidence: list[str]
    action_needed: bool


def render(finding: Finding) -> str:
    params = dict(finding.params)
    if "problem" in params:
        params["problem"] = DATE_PROBLEM_TEXT.get(params["problem"], "could not be used")
    return TEMPLATES[finding.code].format(**params)


def _merge_current_roles(findings: list[Finding]) -> list[Finding]:
    """When both current-role rules fire, ask one question citing the evidence of both."""
    elsewhere = next((f for f in findings if f.code == "CURRENT_ROLE_ELSEWHERE"), None)
    overlapping = next((f for f in findings if f.code == "OVERLAPPING_CURRENT_ROLES"), None)
    if elsewhere is None or overlapping is None:
        return findings
    evidence = list(dict.fromkeys(overlapping.evidence + elsewhere.evidence))
    merged = elsewhere.model_copy(update={"evidence": evidence})
    return [merged if f is elsewhere else f for f in findings if f is not overlapping]


def build_questions(findings: list[Finding]) -> tuple[list[Question], list[Question]]:
    """Return (numbered committee questions, information notes). Uncited findings are dropped."""
    questions: list[Question] = []
    notes: list[Question] = []
    merged_codes = {f.code for f in findings}
    both = {"CURRENT_ROLE_ELSEWHERE", "OVERLAPPING_CURRENT_ROLES"} <= merged_codes
    for finding in _merge_current_roles(findings):
        if not finding.evidence:
            continue
        codes = [finding.code]
        if both and finding.code == "CURRENT_ROLE_ELSEWHERE":
            codes.append("OVERLAPPING_CURRENT_ROLES")
        target = notes if finding.severity == "info" else questions
        target.append(
            Question(
                number=len(target) + 1,
                codes=codes,
                text=render(finding),
                evidence=finding.evidence,
                action_needed=finding.code not in NO_ACTION_CODES,
            )
        )
    return questions, notes
