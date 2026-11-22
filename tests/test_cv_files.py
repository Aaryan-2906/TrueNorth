"""CV file upload. Files are generated here, and all API replies are fake: these tests spend no credits."""

import io
import json
import re

import docx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import cli, cv_files, parse_cv, server
from tests.test_robustness import World, text_reply, url
from tests.test_server import of_kind, read_events

HEADER = ["Name: Expert Q", "Total experience: 9 years", "Employment Record:"]
ROWS = [
    ("Jan 2020 to Present", "Acme Roads Ltd", "Resident Engineer"),
    ("Mar 2015 to Dec 2019", "Beta Infra", "Site Engineer"),
]
FOOTER = ["Certification: I certify that this CV correctly describes me."]
ROW_LINES = [" | ".join(row) for row in ROWS]
CV_TEXT = "\n".join(HEADER + ROW_LINES + FOOTER)


def make_pdf(lines):
    """A small real PDF with one text line per input line."""
    escaped = [line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)") for line in lines]
    body = " T* ".join(f"({line}) Tj" for line in escaped)
    stream = f"BT /F1 9 Tf 12 TL 40 800 Td {body} ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def blank_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def to_bytes(document):
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_with_table():
    document = docx.Document()
    for line in HEADER:
        document.add_paragraph(line)
    table = document.add_table(rows=len(ROWS), cols=3)
    for row, values in zip(table.rows, ROWS):
        for cell, value in zip(row.cells, values):
            cell.text = value
    for line in FOOTER:
        document.add_paragraph(line)
    return to_bytes(document)


def docx_plain():
    document = docx.Document()
    for line in HEADER + ROW_LINES + FOOTER:
        document.add_paragraph(line)
    return to_bytes(document)


def fake_reading(text):
    rows = [
        {"employer": m[2].strip(), "title": m[3].strip(), "start_raw": m[0].strip(), "end_raw": m[1].strip(), "duration_raw": None}
        for m in re.findall(r"^(.+?) to (.+?) \| (.+?) \| (.+?)\s*$", text, re.MULTILINE)
    ]
    total = re.search(r"Total experience: (\d+) years", text)
    return text_reply(
        json.dumps(
            {
                "name": "Expert Q" if rows else None,
                "claimed_total_years": int(total.group(1)) if total else None,
                "claimed_similar_years": None,
                "cv_date": None,
                "profile_url": None,
                "rows": rows,
            }
        )
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    built.llm = fake_reading
    return built


@pytest.fixture
def client():
    return TestClient(server.app)


def claims_for(text):
    return parse_cv.parse_cv(text, "T", None, fresh=True, today=(2026, 11)).model_dump()


def lines_of(text):
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def upload(client, name, data, **fields):
    return client.post("/api/check-cv", files={"file": (name, data)}, data=fields)


# Text extraction


def test_pdf_text_keeps_every_line():
    assert lines_of(cv_files.extract_text("cv.pdf", make_pdf(HEADER + ROW_LINES + FOOTER))) == lines_of(CV_TEXT)


def test_docx_reads_paragraphs_and_table_in_document_order():
    assert cv_files.extract_text("cv.docx", docx_with_table()) == CV_TEXT


def test_docx_merged_cell_appears_once_in_its_row():
    document = docx.Document()
    table = document.add_table(rows=1, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Jan 2020 to Present"
    table.cell(0, 2).text = "Acme"
    assert cv_files.extract_text("cv.docx", to_bytes(document)) == "Jan 2020 to Present | Acme"


def test_docx_multi_paragraph_cell_stays_on_its_row():
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Acme"
    table.cell(0, 1).text = "Resident"
    table.cell(0, 1).add_paragraph("Engineer")
    assert cv_files.extract_text("cv.docx", to_bytes(document)) == "Acme | Resident Engineer"


@pytest.mark.parametrize("name", ["cv.txt", "cv.md", "CV.TXT"])
def test_text_files_are_read_as_they_are(name):
    assert cv_files.extract_text(name, ("﻿" + CV_TEXT).encode("utf-8")) == CV_TEXT


def test_scanned_pdf_gives_the_exact_message():
    with pytest.raises(cv_files.CvFileError) as error:
        cv_files.extract_text("scan.pdf", blank_pdf())
    assert str(error.value) == "This PDF is a scanned image; text could not be read. OCR is not supported yet."


@pytest.mark.parametrize(
    "name, data, message",
    [
        ("cv.pdf", b"not a pdf", "could not be opened"),
        ("cv.docx", b"not a docx", "could not be opened"),
        ("cv.doc", b"old word", "Please upload"),
        ("cv.exe", b"MZ", "Please upload"),
        ("cv.txt", b"x" * (cv_files.MAX_FILE_BYTES + 1), "5 MB"),
    ],
)
def test_bad_files_get_a_clear_message(name, data, message):
    with pytest.raises(cv_files.CvFileError, match=message):
        cv_files.extract_text(name, data)


def test_password_protected_pdf_gets_a_clear_message():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(cv_files.CvFileError, match="password-protected"):
        cv_files.extract_text("locked.pdf", buffer.getvalue())


# Same claims as the text


@pytest.mark.parametrize(
    "name, build",
    [
        ("cv.pdf", lambda: make_pdf(HEADER + ROW_LINES + FOOTER)),
        ("cv.docx", docx_plain),
        ("table.docx", docx_with_table),
    ],
    ids=["pdf", "docx", "docx_table"],
)
def test_file_parses_to_the_same_claims_as_the_text(world, name, build):
    expected = claims_for(CV_TEXT)
    assert len(expected["rows"]) == 2

    assert claims_for(cv_files.extract_text(name, build())) == expected


# The API


@pytest.mark.parametrize(
    "name, build",
    [("cv.pdf", lambda: make_pdf(HEADER + ROW_LINES + FOOTER)), ("table.docx", docx_with_table), ("cv.txt", lambda: CV_TEXT.encode())],
    ids=["pdf", "docx_table", "txt"],
)
def test_upload_runs_the_same_pipeline(world, client, name, build):
    events = read_events(upload(client, name, build(), profile_url=url(1), position="resident_engineer"))

    assert [s["stage"] for s in of_kind(events, "stage")] == [
        "reading_cv", "reading_dates", "looking_up", "checking", "writing", "done",
    ]
    result = events[-1][1]
    assert result["ok"] is True and result["experts"][0]["position"] == "resident_engineer"
    assert [row["employer"] for row in result["experts"][0]["rows"]] == ["Acme Roads Ltd", "Beta Infra"]
    assert world.urls_called() == [url(1)]


def test_json_path_still_works(world, client):
    events = read_events(client.post("/api/check-cv", json={"cv_text": CV_TEXT}))
    assert events[-1][1]["ok"] is True


def test_invalid_json_body_is_a_422(world, client):
    assert client.post("/api/check-cv", json={"nope": 1}).status_code == 422


def test_other_content_types_are_refused(world, client):
    response = client.post("/api/check-cv", content="cv_text=x", headers={"content-type": "text/plain"})
    assert response.status_code == 415


def test_fresh_field_skips_the_cache(world, client):
    data = CV_TEXT.encode()
    upload(client, "cv.txt", data)
    events = read_events(upload(client, "cv.txt", data, fresh="true"))
    assert of_kind(events, "call")[0]["source"] == "live"


def test_scanned_upload_returns_the_message_and_spends_nothing(world, client):
    response = upload(client, "scan.pdf", blank_pdf(), profile_url=url(1))

    assert response.status_code == 422
    assert response.json()["error"] == cv_files.SCANNED_PDF_MESSAGE
    assert world.calls == []


def test_oversized_upload_is_refused(world, client):
    response = upload(client, "big.txt", b"x" * (cv_files.MAX_FILE_BYTES + 1))
    assert response.status_code == 413 and "5 MB" in response.json()["error"]


def test_upload_with_no_file_is_refused(world, client):
    response = client.post("/api/check-cv", files={"other": (None, "x")})
    assert response.status_code == 422


def test_cross_site_upload_is_refused(world, client):
    response = client.post(
        "/api/check-cv",
        files={"file": ("cv.txt", CV_TEXT.encode())},
        data={"profile_url": url(1), "fresh": "true"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403 and world.calls == []


def test_page_has_the_file_zone(client):
    page = client.get("/").text
    for element in ('id="cvDrop"', 'id="cvFile"', 'id="cvFileName"', 'id="cvFileSize"', 'id="cvClear"'):
        assert element in page


# The command line


@pytest.mark.parametrize(
    "name, build",
    [("cv.pdf", lambda: make_pdf(HEADER + ROW_LINES + FOOTER)), ("cv.docx", docx_with_table)],
    ids=["pdf", "docx"],
)
def test_cli_check_cv_reads_files(world, tmp_path, capsys, name, build):
    path = tmp_path / name
    path.write_bytes(build())

    assert cli.main(["check-cv", str(path)]) == 0

    output = capsys.readouterr().out
    assert "Acme Roads Ltd" in output and "Beta Infra" in output


def test_cli_scanned_pdf_gives_the_message(world, tmp_path, capsys):
    path = tmp_path / "scan.pdf"
    path.write_bytes(blank_pdf())

    assert cli.main(["check-cv", str(path)]) == 1

    assert cv_files.SCANNED_PDF_MESSAGE in capsys.readouterr().err
