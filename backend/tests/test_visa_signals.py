

def test_resume_skips_finished_pass_and_continues(session, monkeypatch, tmp_path):
    import json

    from app.pipeline import visa_signals

    path = tmp_path / "visa_signals.json"
    monkeypatch.setattr(visa_signals, "_progress_path", lambda: path)
    path.write_text(json.dumps({"for_you": -1, "others": 0}))
    counts = visa_signals.refresh(others_only=True, resume=True)
    saved = json.loads(path.read_text())
    assert saved["others"] == -1 and saved["for_you"] == -1 and "read" in counts
