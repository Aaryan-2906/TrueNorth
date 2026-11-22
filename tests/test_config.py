from app.config import read_today


def test_today_parses_from_env(monkeypatch):
    monkeypatch.setenv("TODAY", "2027-03")
    assert read_today() == (2027, 3)
