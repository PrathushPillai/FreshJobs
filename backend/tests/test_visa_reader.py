"""The AI visa reader (app/pipeline/visa_reader.py), with a fake Claude client: no API calls."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models import Company, Job, VisaRead
from app.pipeline import visa_reader as vr


def test_passages_keep_only_work_authorization_sentences():
    text = ("We build rockets. You will design avionics.\n"
            "Requirements:\nMust be a U.S. citizen due to ITAR.\nWe offer great benefits. Free lunch.")
    p = vr.passages(text)
    assert "ITAR" in p and "Requirements:" in p  # the sentence before gives it its meaning
    assert "Free lunch" not in p and "avionics" not in p
    assert vr.passages("Great team. Python and SQL.") == ""  # nothing to read: no call


def _message(payload, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=json.dumps(payload))])


VERDICT_NO = {"sponsorship": "no", "opt_cpt": "excluded", "us_persons_only": False, "clearance_required": False,
              "evidence": "We are unable to sponsor visas, including OPT."}


def test_parse_message():
    assert vr.parse_message(_message(VERDICT_NO))["sponsorship"] == "no"
    assert vr.parse_message(_message(VERDICT_NO, stop="refusal")) is None
    assert vr.parse_message(_message({"sponsorship": "maybe"})) is None
    assert vr.parse_message(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="oops")])) is None


@pytest.mark.parametrize("rules,verdict,expected", [
    # The AI adds a flag the rules missed.
    ({"no_sponsor": False, "sponsors_text": False, "student_visa": None}, VERDICT_NO,
     {"no_sponsor": True, "sponsors_text": False, "student_visa": "excluded"}),
    # It never clears one the rules set.
    ({"no_sponsor": True, "sponsors_text": False, "student_visa": None},
     {**VERDICT_NO, "sponsorship": "yes", "opt_cpt": "not_mentioned"},
     {"no_sponsor": True, "sponsors_text": False, "student_visa": None}),
    # "Says it sponsors", from the AI alone.
    ({"no_sponsor": False, "sponsors_text": False, "student_visa": None},
     {**VERDICT_NO, "sponsorship": "yes", "opt_cpt": "welcome"},
     {"no_sponsor": False, "sponsors_text": True, "student_visa": "welcome"}),
    # A clearance requirement rules sponsorship out.
    ({"no_sponsor": False, "sponsors_text": True, "student_visa": None},
     {**VERDICT_NO, "sponsorship": "unclear", "opt_cpt": "not_mentioned", "clearance_required": True},
     {"no_sponsor": True, "sponsors_text": False, "student_visa": None}),
    # Nothing read: the rules stand.
    ({"no_sponsor": False, "sponsors_text": True, "student_visa": "welcome"}, None,
     {"no_sponsor": False, "sponsors_text": True, "student_visa": "welcome"}),
])
def test_overlay_only_adds(rules, verdict, expected):
    assert vr.overlay(rules, verdict) == expected


def test_request_params_use_structured_output_and_cache_the_instructions():
    p = vr.request_params("Engineer", "Acme", "Must be a US citizen.", model="claude-opus-5-5")
    assert p["output_config"]["format"]["schema"] is vr.SCHEMA
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Must be a US citizen." in p["messages"][0]["content"]


class FakeBatches:
    def __init__(self):
        self.sent = []

    def create(self, requests):
        self.sent.append(requests)
        return SimpleNamespace(id=f"batch_{len(self.sent)}")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status="ended")

    def results(self, batch_id):
        for request in self.sent[int(batch_id.split("_")[1]) - 1]:
            yield SimpleNamespace(custom_id=request["custom_id"],
                                  result=SimpleNamespace(type="succeeded", message=_message(VERDICT_NO)))


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    batches = FakeBatches()
    monkeypatch.setattr(vr, "client", lambda: SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    monkeypatch.setattr(vr, "_state_file", lambda: tmp_path / "visa_reader.json")
    monkeypatch.setattr(vr.settings, "anthropic_api_key", "test-key")
    return batches


def test_submit_then_collect_flags_the_job(session, fake_api):
    now = datetime.now(timezone.utc)
    c = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(c)
    session.flush()
    job = Job(company_id=c.id, external_id="1", title="Data Analyst", url="https://x/1", fingerprint="f",
              description="Great role. We are unable to sponsor visas at this time, including F-1 OPT.",
              first_seen_at=now, last_seen_at=now, role_match=True, us_eligible=True, no_sponsor=False)
    plain = Job(company_id=c.id, external_id="2", title="Data Analyst", url="https://x/2", fingerprint="g",
                description="Python and SQL. Great team.", first_seen_at=now, last_seen_at=now, role_match=True)
    session.add_all([job, plain])
    session.commit()

    assert vr.submit(session)["sent"] == 1  # the posting with nothing about visas isn't sent
    assert vr.submit(session)["sent"] == 0  # already in a running batch
    assert vr.collect(session) == {"stored": 1, "failed": 0, "pending_batches": 0}
    session.refresh(job)
    assert job.no_sponsor and job.student_visa == "excluded"
    read = session.get(VisaRead, job.id)
    assert read.verdict["evidence"] and read.changed


def test_daily_cap(session, fake_api, monkeypatch):
    monkeypatch.setattr(vr.settings, "visa_reader_daily_jobs", 0)
    assert vr.submit(session)["sent"] == 0


def test_without_a_key_nothing_runs(session, monkeypatch):
    monkeypatch.setattr(vr.settings, "anthropic_api_key", "")
    assert "skipped" in vr.run_once(session)


def test_groq_reads_due_jobs_within_the_daily_budget(session, monkeypatch, tmp_path, respx_mock):
    import httpx

    monkeypatch.setattr(vr, "_state_file", lambda: tmp_path / "visa_reader.json")
    monkeypatch.setattr(vr.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(vr.settings, "visa_reader_provider", "groq")
    monkeypatch.setattr(vr.settings, "visa_reader_groq_daily_tokens", 1500)
    now = datetime.now(timezone.utc)
    c = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(c)
    session.flush()
    jobs = [Job(company_id=c.id, external_id=str(i), title="Data Analyst", url=f"https://x/{i}", fingerprint=f"f{i}",
                description="Work Authorization: Sponsorship Available: No.", first_seen_at=now, last_seen_at=now,
                role_match=True, us_eligible=True, no_sponsor=False) for i in range(3)]
    session.add_all(jobs)
    session.commit()
    route = respx_mock.post(vr.GROQ_URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(VERDICT_NO)}}],
        "usage": {"total_tokens": 1000}}))

    out = vr.read_due_with_groq(session, sleep=lambda s: None)
    assert route.call_count == 2 and out["read"] == 2  # the second read passes the 1,500-token budget: stop
    assert out["stopped"] == "daily token budget reached"
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["json_schema"]["strict"] is True and sent["reasoning_effort"] == "low"
    assert "Sponsorship Available" in sent["messages"][1]["content"]
    flagged = [j for j in jobs if (session.refresh(j) or j.no_sponsor)]
    assert len(flagged) == 2
    assert vr.read_due_with_groq(session, sleep=lambda s: None)["reason"].startswith("today's Groq token budget")


def test_groq_rate_limit_stops_the_run(session, monkeypatch, tmp_path, respx_mock):
    import httpx

    monkeypatch.setattr(vr, "_state_file", lambda: tmp_path / "visa_reader.json")
    monkeypatch.setattr(vr.settings, "groq_api_key", "test-key")
    now = datetime.now(timezone.utc)
    c = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(c)
    session.flush()
    session.add(Job(company_id=c.id, external_id="1", title="Data Analyst", url="https://x/1", fingerprint="f",
                    description="Must be a US citizen.", first_seen_at=now, last_seen_at=now, role_match=True,
                    us_eligible=True, no_sponsor=False))
    session.commit()
    respx_mock.post(vr.GROQ_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "30"}))
    out = vr.read_due_with_groq(session, sleep=lambda s: None)
    assert out["read"] == 0 and out["stopped"] == "Groq answered HTTP 429"
    assert session.query(VisaRead).count() == 0  # read again next run
