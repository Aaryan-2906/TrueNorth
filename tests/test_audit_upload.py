"""POST /api/audit-upload: a bid built in memory from uploaded CVs. No paid calls."""

import io

import docx
import pytest
from fastapi.testclient import TestClient

from app import config, cv_files, firm, server
from tests.test_robustness import World
from tests.test_server import of_kind, read_events

FIRM_RECORD = {
    "crustdata_company_id": 77,
    "name": "Test Roads Ltd",
    "primary_domain": "testroads.example",
    "employee_count_range": "11-50",
    "headcount": None,
}


def cv(name, years, employer):
    return (
        "Proposed Position: Team Leader cum Senior Highway Engineer\n"
        f"Name of Staff: {name}\n"
        f"Key Qualifications: Civil engineer with over {years} years of experience in highways.\n\n"
        "Employment Record:\n"
        f"01/2015 to 03/2022 | {employer} | Team Leader\n"
        "Place: Pune   Date: 15/04/2025\n"
    )


def docx_bytes(text):
    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def no_ai(_text):
    raise AssertionError("the AI reader must not be called")


def post(client, files, firm_name="Test Roads Ltd", headers=None, **fields):
    data = {"firm_name": firm_name, **fields}
    parts = [("file", item) for item in files]
    return client.post("/api/audit-upload", data=data, files=parts, headers=headers)


def text_files(count):
    return [(f"cv{n}.md", cv(f"Expert {n}", 10 + n, f"Employer {n}").encode(), "text/markdown") for n in range(1, count + 1)]


def all_files(root):
    return {path.relative_to(root).as_posix(): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    built.llm = no_ai
    built.firm_calls = []

    def fake_lookup(bid, fresh=None):
        built.firm_calls.append(bid["firm"])
        return [FIRM_RECORD], None

    monkeypatch.setattr(firm, "lookup_firm", fake_lookup)
    return built


@pytest.fixture
def client():
    return TestClient(server.app)


def test_two_generated_cvs_are_audited_with_no_paid_calls(world, client):
    files = [
        ("first.docx", docx_bytes(cv("Expert A", 20, "Alpha Roads")), "application/octet-stream"),
        ("second.md", cv("Expert B", 12, "Beta Infra").encode(), "text/markdown"),
    ]

    response = post(
        client, files, firm_domain="https://www.testroads.example/about",
        position=["team_leader", ""], employment=["full_time", "associate"],
    )

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = read_events(response)
    stages = of_kind(events, "stage")
    assert stages[-2]["stage"] == "writing" and stages[-1]["stage"] == "done"
    result = events[-1][1]
    assert result["ok"] is True and result["kind"] == "audit"
    assert [e["id"] for e in result["experts"]] == ["C1", "C2"]
    assert [e["name"] for e in result["experts"]] == ["Expert A", "Expert B"]
    assert [e["read_by"] for e in result["experts"]] == ["rules", "rules"]
    assert [e["position"] for e in result["experts"]] == ["team_leader", None]
    assert result["experts"][0]["rows"][0]["employer"] == "Alpha Roads"
    assert result["experts"][0]["lookup"] == "none (no profile URL supplied)"
    assert result["firm"]["name"] == "Test Roads Ltd" and result["firm"]["domain"] == "testroads.example"
    assert result["firm"]["full_time_people"] == 1
    assert result["report_url"] == "/api/report.html" and result["credits"] == 0
    assert world.calls == [] and of_kind(events, "call") == []
    assert world.firm_calls == [{"name": "Test Roads Ltd", "domain": "testroads.example"}]


def test_result_has_the_same_shape_as_a_saved_audit(world, client):
    world.add_expert("E1", cv=cv("Expert A", 20, "Alpha Roads"))
    world.write_bid()
    saved = read_events(client.post("/api/audit", json={"bid": str(world.bid_dir / "bid.json")}))[-1][1]

    uploaded = read_events(post(client, text_files(1)))[-1][1]

    assert set(uploaded) == set(saved)
    assert set(uploaded["experts"][0]) == set(saved["experts"][0])
    assert set(uploaded["firm"]) == set(saved["firm"])


def test_nothing_is_written_to_data(world, client):
    real_data = config.ROOT_DIR / "data"
    before = all_files(real_data), all_files(world.data)

    assert read_events(post(client, text_files(2)))[-1][1]["ok"] is True

    assert (all_files(real_data), all_files(world.data)) == before


def test_six_files_are_accepted(world, client):
    result = read_events(post(client, text_files(6)))[-1][1]

    assert [e["id"] for e in result["experts"]] == ["C1", "C2", "C3", "C4", "C5", "C6"]


@pytest.mark.parametrize("count", [7, 9])
def test_more_than_six_files_are_refused(world, client, count):
    response = post(client, text_files(count))

    assert response.status_code == 422 and "at most 6" in response.json()["error"]
    assert world.firm_calls == []


@pytest.mark.parametrize("name", ["notes.exe", "cv.doc", "no_suffix"])
def test_wrong_file_type_is_refused_before_anything_runs(world, client, name):
    files = [text_files(1)[0], (name, b"some text", "application/octet-stream")]

    response = post(client, files)

    assert response.status_code == 422 and response.json()["error"].startswith("File 2:")
    assert world.calls == [] and world.firm_calls == []


def test_oversized_file_is_refused(world, client):
    big = ("big.txt", b"x" * (cv_files.MAX_FILE_BYTES + 1), "text/plain")

    response = post(client, [text_files(1)[0], big])

    assert response.status_code == 413 and response.json()["error"] == "File 2 is larger than the 5 MB limit."


def test_unreadable_file_is_reported_and_the_rest_continue(world, client):
    files = [("damaged.pdf", b"%PDF-not really", "application/pdf"), text_files(1)[0]]

    result = read_events(post(client, files))[-1][1]

    first, second = result["experts"]
    assert first["status"] == "CANNOT_CHECK" and first["rows"] == []
    assert "uploaded CV 1" in first["questions"][0]["text"] and "damaged" in first["questions"][0]["text"]
    assert "damaged.pdf" not in str(result)
    assert second["read_by"] == "rules" and second["rows"]


def test_same_name_in_two_cvs_raises_the_duplicate_question(world, client):
    same = cv("Expert A", 20, "Alpha Roads").encode()
    files = [("a.md", same, "text/markdown"), ("b.md", same, "text/markdown")]

    result = read_events(post(client, files))[-1][1]

    assert result["firm"]["full_time_people"] == 1
    assert any("C1 and C2" in q["text"] for q in result["firm"]["questions"])


def test_cross_site_upload_is_refused(world, client):
    response = post(client, text_files(1), headers={"origin": "https://evil.example"})

    assert response.status_code == 403 and world.firm_calls == []


def test_same_site_origin_is_accepted(world, client):
    assert post(client, text_files(1), headers={"origin": "http://testserver"}).status_code == 200


def test_json_is_refused(world, client):
    assert client.post("/api/audit-upload", json={"firm_name": "x"}).status_code == 415


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"firm_name": "   "}, "firm name"),
        ({"firm_domain": "not a domain"}, "firm domain"),
        ({"profile_url": "https://example.com/in/someone"}, "File 1"),
        ({"position": "astronaut"}, "File 1"),
        ({"employment": "intermittent"}, "File 1"),
        ({"position": ["team_leader", "team_leader"]}, "own profile URL"),
    ],
)
def test_bad_fields_are_refused(world, client, fields, message):
    response = post(client, text_files(1), **{"firm_name": "Firm", **fields})

    assert response.status_code == 422 and message in response.json()["error"]
    assert world.calls == [] and world.firm_calls == []


def test_no_file_is_refused(world, client):
    response = client.post("/api/audit-upload", data={"firm_name": "Firm"}, files={"other": ("x.txt", b"x")})

    assert response.status_code == 422 and "at least one CV" in response.json()["error"]


def test_page_has_the_upload_mode(client):
    page = client.get("/").text

    for marker in ('id="bidModeUpload"', 'id="firmName"', 'id="bidFiles"', "/api/audit-upload", "file.name"):
        assert marker in page
