

def test_resume_skips_finished_pass_and_continues(session, monkeypatch, tmp_path):
    import json

    from app.pipeline import visa_signals

    path = tmp_path / "visa_signals.json"
    monkeypatch.setattr(visa_signals, "_progress_path", lambda tag="": path)
    path.write_text(json.dumps({"for_you": -1, "others": 0}))
    counts = visa_signals.refresh(others_only=True, resume=True)
    saved = json.loads(path.read_text())
    assert saved["others"] == -1 and saved["for_you"] == -1 and "read" in counts



def test_id_range_reads_only_its_slice(session, monkeypatch, tmp_path):
    from app.models import Company, Job
    from app.pipeline import visa_signals

    monkeypatch.setattr(visa_signals, "_progress_path", lambda tag="": tmp_path / f"p{tag}.json")
    company = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(company)
    session.flush()
    desc = "We will not sponsor visas for this role. " * 5
    for i in range(1, 7):
        session.add(Job(id=i, company_id=company.id, external_id=str(i), title="Engineer", url="u", description=desc, fingerprint=f"f{i}",
                        described=True, role_match=False, no_sponsor=False))
    session.commit()
    counts = visa_signals.refresh(others_only=True, id_range=(2, 4), tag="w0")
    assert counts["read"] == 2  # ids 3 and 4
    session.expire_all()
    assert [j.id for j in session.query(Job).filter_by(no_sponsor=True).order_by(Job.id)] == [3, 4]
