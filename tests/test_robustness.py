"""Failure cases judges may try. All API replies are fake: these tests spend no credits."""

import csv
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest

from app import cli, config, crustdata, parse_cv, report

ROOT = Path(__file__).resolve().parent.parent
CV = "Name: Expert\nTotal experience: 5 years\nFrom Jan 2020 to Present: Acme, Engineer\n"
RECIPE = "Banana bread\n2 cups flour, 3 ripe bananas, 1 egg.\nMix, pour into a tin and bake for an hour.\n"
TIMEOUT_TEXT = "lookup did not complete: it may have been billed, so it was not retried"


def extract_from_cv(text):
    rows = [
        {"employer": m[2], "title": m[3], "start_raw": m[0], "end_raw": m[1], "duration_raw": None}
        for m in re.findall(r"From (.+?) to (.+?): ([^,\n]+), (.+)", text)
    ]
    total = re.search(r"Total experience: (\d+) years", text)
    return {
        "name": "Expert" if rows else None,
        "claimed_total_years": int(total.group(1)) if total else None,
        "claimed_similar_years": None,
        "cv_date": None,
        "profile_url": None,
        "rows": rows,
    }


def text_reply(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def person_match(name="Expert One"):
    job = {"name": "Acme", "title": "Engineer", "start_date": "2020-01-01T00:00:00+00:00", "end_date": None}
    old = {"name": "Old Co", "title": "Engineer", "start_date": "2015-01-01T00:00:00+00:00", "end_date": "2019-12-01T00:00:00+00:00"}
    return {
        "confidence_score": 0.9,
        "person_data": {
            "crustdata_person_id": 1,
            "basic_profile": {"name": name},
            "experience": {"employment_details": {"current": [job], "past": [old]}},
            "education": {"schools": []},
            "social_handles": {},
        },
    }


def company_match(company_id=7, name="Acme", size="11-50"):
    return {
        "confidence_score": 1.0,
        "company_data": {
            "crustdata_company_id": company_id,
            "basic_info": {"name": name, "primary_domain": "acme.test", "employee_count_range": size},
        },
    }


class World:
    """A temporary bid, cache, ledger and output folder, with fake Crustdata and LLM services."""

    def __init__(self, tmp_path, monkeypatch):
        self.data = tmp_path / "data"
        self.bid_dir = self.data / "bid"
        (self.bid_dir / "cvs").mkdir(parents=True)
        (self.bid_dir / "claims").mkdir()
        shutil.copy(ROOT / "data" / "tender.json", self.data / "tender.json")
        self.out = tmp_path / "out" / "report.md"
        self.ledger = tmp_path / "credits.csv"
        self.experts = []
        self.calls = []
        self.http = self.default_http
        self.llm = lambda text: text_reply(json.dumps(extract_from_cv(text)))

        for target, name, value in [
            (cli, "DATA_DIR", self.data),
            (cli, "BID_DIR", self.bid_dir),
            (crustdata, "CACHE_DIR", tmp_path / "cache"),
            (crustdata, "LEDGER_PATH", self.ledger),
            (crustdata, "RETRY_WAIT_SECONDS", 0),
            (crustdata, "_lookups", []),
            (parse_cv, "CACHE_DIR", tmp_path / "cache" / "llm"),
            (parse_cv, "_readings", []),
            (report, "OUT_PATH", self.out),
            (config, "CRUSTDATA_API_KEY", "test-key"),
            (config, "ANTHROPIC_API_KEY", "test-key"),
            (config, "FRESH", False),
            (config, "TODAY", (2026, 11)),
        ]:
            monkeypatch.setattr(target, name, value)
        monkeypatch.setattr(crustdata.httpx, "request", self.fake_request)
        monkeypatch.setattr(parse_cv.anthropic, "Anthropic", self.fake_anthropic)

    def fake_anthropic(self, **_kwargs):
        def create(**request):
            content = request["messages"][0]["content"]
            return self.llm(content.split("<cv>\n", 1)[1].split("\n</cv>", 1)[0])

        return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))

    def default_http(self, url, body):
        if url.endswith("/person/enrich"):
            profile = body["professional_network_profile_urls"][0]
            return 200, [{"matched_on": profile, "match_status": "matched", "matches": [person_match()]}]
        return 200, [{"matched_on": "acme.test", "match_type": "domain", "matches": [company_match()]}]

    def fake_request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((url, json))
        status, reply = self.http(url, json)
        return httpx.Response(
            status, headers={"x-credits-used": "1"}, json=reply, request=httpx.Request(method, url)
        )

    def add_expert(self, expert_id, url=None, cv=CV, position=None, employment="associate"):
        (self.bid_dir / "cvs" / f"{expert_id}.md").write_text(cv, encoding="utf-8")
        self.experts.append(
            {
                "id": expert_id,
                "name": f"Person {expert_id}",
                "proposed_position": position,
                "profile_url": url,
                "employment_with_bidder": employment,
                "cv": f"cvs/{expert_id}.md",
                "claims": f"claims/{expert_id}.json",
            }
        )

    def write_bid(self, **changes):
        bid = {"firm": {"name": "Acme", "domain": "acme.test"}, "experts": self.experts, **changes}
        (self.bid_dir / "bid.json").write_text(json.dumps(bid), encoding="utf-8")

    def audit(self, *args):
        crustdata._lookups.clear()
        parse_cv._readings.clear()
        self.write_bid()
        return cli.main(["audit", *args])

    def report(self):
        return self.out.read_text(encoding="utf-8")

    def section(self, expert_id):
        text = self.report()
        start = text.index(f"### {expert_id}:")
        ends = [text.find(mark, start + 1) for mark in ("\n### ", "\n## ")]
        return text[start : min(end for end in ends if end != -1)]

    def urls_called(self, suffix="/person/enrich"):
        return [body["professional_network_profile_urls"][0] for url, body in self.calls if url.endswith(suffix)]

    def ledger_rows(self):
        with self.ledger.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def url(number):
    return f"https://www.linkedin.com/in/person-{number}"


def four_experts(world):
    for number in range(1, 5):
        world.add_expert(f"E{number}", url(number))


def time_out_on(target):
    def decide(world):
        original = world.default_http

        def handler(call_url, body):
            if call_url.endswith("/person/enrich") and body["professional_network_profile_urls"][0] == target:
                raise httpx.ReadTimeout("timed out")
            return original(call_url, body)

        return handler

    return decide


# 1. A failed call resumes without a double charge


def test_timeout_on_third_lookup_keeps_results_and_finishes(world):
    four_experts(world)
    world.http = time_out_on(url(3))(world)

    assert world.audit() == 0

    assert world.urls_called() == [url(1), url(2), url(3), url(4)]
    assert TIMEOUT_TEXT in world.section("E3")
    for expert_id in ("E1", "E2", "E4"):
        assert "**Lookup:** live" in world.section(expert_id)
    assert world.out.with_suffix(".html").exists()


def test_timed_out_call_is_in_the_ledger_as_possibly_billed(world):
    four_experts(world)
    world.http = time_out_on(url(3))(world)
    world.audit()

    row = next(r for r in world.ledger_rows() if r["status"] == "ReadTimeout")
    assert row["credits_used"] == ""
    assert "may also have been billed" in world.report()


def test_rerun_after_timeout_does_not_rebill_cached_lookups(world):
    four_experts(world)
    world.http = time_out_on(url(3))(world)
    world.audit()
    world.calls.clear()
    world.http = world.default_http

    assert world.audit() == 0

    assert world.urls_called() == [url(3)]
    assert world.urls_called("/company/identify") == []
    for expert_id in ("E1", "E2", "E4"):
        assert "**Lookup:** from cache" in world.section(expert_id)


# 2. The report states what was live and what was reused


def test_fresh_run_reports_zero_lookups_from_cache(world):
    four_experts(world)
    world.audit()

    assert world.audit("--fresh") == 0

    assert "Lookups this run: 5 live, 0 from cache." in world.report()
    assert "CV readings this run: 4 by the LLM, 0 from cache." in world.report()


def test_normal_run_shows_reused_lookups_and_fetch_times(world):
    four_experts(world)
    world.audit()

    assert world.audit() == 0

    text = world.report()
    assert "Lookups this run: 0 live, 5 from cache." in text
    assert len(re.findall(r"/person/enrich: from cache, fetched \d{4}-\d\d-\d\dT", text)) == 4
    assert re.search(r"/company/identify: from cache, fetched \d{4}-\d\d-\d\dT", text)


# 3. Malformed input gives a clear message that names the file and the problem


def write_claims(world, name, **changes):
    claims = {
        "expert_id": "E1",
        "name": "Expert",
        "proposed_position": None,
        "claimed_total_years": 5,
        "claimed_similar_years": None,
        "rows": [{"employer": "Acme", "title": "Engineer", "start": "2020-01", "end": "present"}],
    }
    claims.update(changes)
    path = world.bid_dir / "claims" / name
    path.write_text(json.dumps(claims), encoding="utf-8")
    return path


def test_claims_file_with_bad_date_names_file_and_field(world):
    path = write_claims(
        world, "bad.json", rows=[{"employer": "A", "title": "T", "start": "13th of nothing", "end": "present"}]
    )
    with pytest.raises(cli.InputError, match=r"bad\.json.*rows\.0\.start"):
        cli.load_claims(path)


def test_claims_file_with_unknown_position_names_file_and_value(world):
    path = write_claims(world, "pos.json", proposed_position="astronaut")
    with pytest.raises(cli.InputError, match=r"pos\.json.*astronaut"):
        cli.load_claims(path)


def test_bid_with_unknown_position_stops_before_any_paid_call(world, capsys):
    world.add_expert("E1", url(1), position="astronaut")

    assert world.audit() == 2

    error = capsys.readouterr().err
    assert "bid.json" in error and "E1" in error and "astronaut" in error
    assert world.calls == []


@pytest.mark.parametrize("field", ["id", "name", "proposed_position", "employment_with_bidder", "cv"])
def test_bid_expert_with_missing_field_is_named(world, capsys, field):
    world.add_expert("E1")
    del world.experts[0][field]

    assert world.audit() == 2

    error = capsys.readouterr().err
    assert "bid.json" in error and field in error


@pytest.mark.parametrize("missing", ["firm", "experts"])
def test_bid_with_missing_top_level_field_is_named(world, capsys, missing):
    world.add_expert("E1")
    world.write_bid()
    bid = json.loads((world.bid_dir / "bid.json").read_text(encoding="utf-8"))
    del bid[missing]
    (world.bid_dir / "bid.json").write_text(json.dumps(bid), encoding="utf-8")

    assert cli.main(["audit"]) == 2

    error = capsys.readouterr().err
    assert "bid.json" in error and missing in error


def test_bid_that_is_not_json_names_the_file(world, capsys):
    (world.bid_dir / "bid.json").write_text("{not json", encoding="utf-8")

    assert cli.main(["audit"]) == 2

    assert "bid.json" in capsys.readouterr().err


def test_bid_cv_path_outside_the_bid_folder_is_refused(world, capsys):
    world.add_expert("E1")
    world.experts[0]["cv"] = "../../secret.md"

    assert world.audit() == 2

    assert "cv" in capsys.readouterr().err


def test_check_cv_with_unknown_position_names_it(world, capsys, tmp_path):
    path = tmp_path / "one.md"
    path.write_text(CV, encoding="utf-8")

    assert cli.main(["check-cv", str(path), "--position", "astronaut"]) == 2

    assert "astronaut" in capsys.readouterr().err


def test_missing_cv_file_is_reported_and_run_continues(world):
    world.add_expert("E1", url(1))
    world.add_expert("E2", url(2))
    (world.bid_dir / "cvs" / "E1.md").unlink()

    assert world.audit() == 0

    assert "CV could not be read" in world.section("E1") and "E1.md" in world.section("E1")
    assert "**Status:** CONSISTENT" not in world.section("E1")
    assert world.urls_called() == [url(2)]


def test_empty_cv_is_reported_and_run_continues(world):
    world.add_expert("E1", url(1), cv="   \n")
    world.add_expert("E2", url(2))

    assert world.audit() == 0

    assert "CV could not be read" in world.section("E1") and "empty" in world.section("E1")
    assert "**Status:** CANNOT_CHECK" in world.section("E1")
    assert "Acme" in world.section("E2")


def test_recipe_is_reported_as_not_a_cv(world):
    world.add_expert("E1", url(1), cv=RECIPE)
    world.add_expert("E2", url(2))

    assert world.audit() == 0

    section = world.section("E1")
    assert "CV could not be read" in section and "does not look like a CV" in section
    assert world.urls_called() == [url(2)]


def test_check_cv_on_a_recipe_gives_a_clear_message(world, capsys, tmp_path):
    path = tmp_path / "recipe.txt"
    path.write_text(RECIPE, encoding="utf-8")

    assert cli.main(["check-cv", str(path)]) == 1

    error = capsys.readouterr().err
    assert "recipe.txt" in error and "does not look like a CV" in error


def test_cv_with_a_bad_date_raises_a_question_not_a_crash(world):
    world.add_expert("E1", cv="Total experience: 1 years\nFrom Janvember 2020 to Present: Acme, Engineer\n")

    assert world.audit() == 0

    assert "CV_DATE_INVALID" in world.section("E1") or "CV_DATE_INVALID" in world.report()


def test_bid_with_zero_experts_says_so(world):
    assert world.audit() == 0

    text = world.report()
    assert "this bid lists no experts" in text.lower()
    assert world.out.with_suffix(".html").exists()


# 4. Several firm records


def test_multiple_firm_records_show_all_ids_and_names(world):
    world.add_expert("E1")
    original = world.default_http

    def handler(call_url, body):
        if call_url.endswith("/company/identify"):
            matches = [company_match(101, "Acme Pvt Ltd", "201-500"), company_match(202, "Acme Group", "myself only")]
            return 200, [{"matched_on": "acme.test", "matches": matches}]
        return original(call_url, body)

    world.http = handler
    assert world.audit() == 0

    firm_section = world.report().split("## Firm and bid")[1].split("## Experts")[0]
    assert "may be the same firm" in firm_section
    for value in ("101", "202", "Acme Pvt Ltd", "Acme Group"):
        assert value in firm_section


# 5. Two people match one URL


def test_two_matches_for_one_url_shows_only_the_count(world):
    world.add_expert("E1", url(1))
    original = world.default_http

    def handler(call_url, body):
        if call_url.endswith("/person/enrich"):
            matches = [person_match("Stranger Alpha"), person_match("Stranger Beta")]
            return 200, [{"matched_on": url(1), "match_status": "matched", "matches": matches}]
        return original(call_url, body)

    world.http = handler
    assert world.audit() == 0

    text = world.report()
    assert "AMBIGUOUS_IDENTITY" in text and "2 profiles match" in text
    assert "Stranger" not in text and "Old Co" not in text


# 6. LLM failures


def api_error(_text):
    raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.test/v1/messages"))


def refusal(_text):
    return text_reply("", stop_reason="refusal")


def invalid_json(_text):
    return text_reply("this is not json")


@pytest.mark.parametrize("failure", [api_error, refusal, invalid_json], ids=["api_error", "refusal", "invalid_json"])
def test_llm_failure_is_reported_and_run_continues(world, failure):
    bad_cv = CV + "Marker for the failing CV\n"
    world.add_expert("E1", url(1), cv=bad_cv)
    world.add_expert("E2", url(2))
    healthy = world.llm
    world.llm = lambda text: failure(text) if "Marker" in text else healthy(text)

    assert world.audit() == 0

    assert "CV could not be read" in world.section("E1")
    assert "**Status:** CANNOT_CHECK" in world.section("E1")
    assert "**Status:** CANNOT_CHECK" not in world.section("E2")
    assert world.urls_called() == [url(2)]


def test_invalid_json_is_retried_exactly_once(world):
    world.add_expert("E1")
    attempts = []

    def flaky(text):
        attempts.append(text)
        return invalid_json(text) if len(attempts) == 1 else text_reply(json.dumps(extract_from_cv(text)))

    world.llm = flaky
    assert world.audit() == 0

    assert len(attempts) == 2
    assert "CV could not be read" not in world.section("E1")


def test_corrupt_llm_cache_is_read_again(world):
    world.add_expert("E1")
    world.audit()
    cached = next((world.out.parent.parent / "cache" / "llm").glob("*.json"))
    cached.write_text("broken", encoding="utf-8")

    assert world.audit() == 0

    assert "CV could not be read" not in world.section("E1")


# 7. Crustdata errors


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "CRUSTDATA_API_KEY"),
        (402, "credit"),
        (403, "/account/endpoints"),
        (429, "rate limit"),
    ],
)
def test_crustdata_http_errors_give_clear_messages(world, capsys, status, expected):
    world.add_expert("E1", url(1))
    world.http = lambda _url, _body: (status, {"error": "raw server text"})

    assert world.audit() == 0

    text = world.report()
    assert f"HTTP {status}" in text and expected in text
    assert "raw server text" not in text
    assert "not found in Crustdata" not in text
    assert f"HTTP {status}" in capsys.readouterr().err


def test_rate_limit_retries_three_times_then_stops(world):
    world.add_expert("E1", url(1))
    world.http = lambda _url, _body: (429, {"error": "slow down"})

    world.audit()

    assert len(world.urls_called()) == 4


def test_unexpected_reply_shape_is_a_failed_lookup(world):
    world.add_expert("E1", url(1))
    original = world.default_http
    world.http = lambda u, b: (200, {"surprise": True}) if u.endswith("/person/enrich") else original(u, b)

    assert world.audit() == 0

    assert "unexpected" in world.section("E1")


def test_corrupt_crustdata_cache_is_not_paid_for_again(world):
    world.add_expert("E1", url(1))
    world.audit()
    cached = next((world.out.parent.parent / "cache" / "person_enrich").glob("*.json"))
    cached.write_text("broken", encoding="utf-8")
    world.calls.clear()

    assert world.audit() == 0

    assert world.urls_called() == []
    assert "could not be read" in world.section("E1")
