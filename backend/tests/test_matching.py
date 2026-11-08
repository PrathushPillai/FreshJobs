import base64
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import matching
from app.main import app
from app.models import Company
from app.pipeline import scoring
from app.pipeline.poller import process_company
from app.prefs import get_prefs
from app.sources import NormalizedJob

RESUME = """Jane Doe — Data Engineer
Master of Science in Data Science, Northeastern University, Sep 2023 – May 2025
Data Engineer Intern, Acme Corp, Jun 2024 – Aug 2024: ETL pipelines in Python and SQL with Airflow on AWS; dbt on Snowflake.
Data Analyst, Infosys, Jul 2020 – Jul 2023: SQL Server, Power BI, Excel, Python, A/B testing.
Skills: Python, SQL, Spark, Docker, Git, Tableau
"""


def fake_embed(texts):
    """A stand-in for the model: bag-of-skills vectors, so closer skill sets give a higher cosine."""
    names = list(matching.SKILLS)
    out = []
    for t in texts:
        found = set(matching.extract_skills(t))
        v = np.array([1.0 if n in found else 0.0 for n in names] + [0.3], dtype=np.float32)
        out.append(v / np.linalg.norm(v))
    return np.array(out)


def test_resume_reading():
    assert matching.resume_years(RESUME, today=date(2026, 11, 7)) == 3.3  # the education dates don't count
    assert matching.resume_degree(RESUME) == "masters"
    assert {"Python", "SQL", "Airflow", "dbt", "Snowflake", "Spark"} <= set(matching.extract_skills(RESUME))
    assert matching.resume_roles(RESUME)[:2] == ["data engineer", "data analyst"]
    assert "Go" not in matching.extract_skills("Let's go! We go fast.")
    assert "R" in matching.extract_skills("Strong R programming") and "R" not in matching.extract_skills("Plan R&D")


def test_score_parts_and_reasons():
    profile = {"skills": ["Python", "SQL", "Airflow", "dbt"], "years": 2, "degree": "masters",
               "target_roles": ["data engineer"], "needs_sponsorship": True, "locations": []}
    job = {"title": "Data Engineer", "description": "You will join the analytics team and work on our platform with product groups. "
           "Python, SQL, Airflow, Kafka. 3+ years.", "min_years": 3,
           "no_sponsor": False, "student_visa": None, "remote": False, "places": []}
    good = matching.score(profile, job, 0.84)
    assert good["matched"] == ["Python", "SQL", "Airflow"] and good["missing"] == ["Kafka"]
    assert good["parts"]["skills"] == 75 and good["parts"]["role"] == 100 and good["parts"]["fit"] == 100
    assert "asks 3+ years; you have 2" in good["notes"]
    no_sponsor = matching.score(profile, {**job, "no_sponsor": True}, 0.84)
    assert no_sponsor["score"] < good["score"] * 0.6 and "rules out sponsorship" in no_sponsor["notes"]
    other = matching.score(profile, {**job, "title": "Registered Nurse", "description": "Patient care, EMR."}, 0.6)
    assert other["score"] < 45 < good["score"]
    untold = matching.score(profile, {**job, "description": ""}, 0.84)  # a title alone is never a strong match
    assert untold["score"] <= matching.UNDESCRIBED_CAP and "no description yet: scored from the title only" in untold["notes"]


@pytest.fixture()
def client(session, now, monkeypatch):
    monkeypatch.setattr(matching, "embed", fake_embed)
    acme = Company(name="Acme", ats="greenhouse", slug="acme")
    session.add(acme)
    session.flush()
    prefs = get_prefs(session)
    prefs.include_keywords = ["data engineer", "nurse"]
    process_company(session, acme, [], prefs, now)
    process_company(session, acme, [
        NormalizedJob(external_id="1", title="Data Engineer", url="u1", locations=["Boston, MA"],
                      description="Python, SQL, Airflow, dbt, Snowflake, AWS. 2+ years of experience."),
        NormalizedJob(external_id="2", title="Senior Data Engineer", url="u2", locations=["Boston, MA"],
                      description="Scala, Kafka, Flink, Kubernetes, Go. 8+ years of experience."),
        NormalizedJob(external_id="3", title="Registered Nurse", url="u3", locations=["Boston, MA"],
                      description="Patient care and charting. Must be a US citizen."),
    ], prefs, now)
    session.commit()
    with TestClient(app) as c:
        yield c


def test_upload_scores_and_sorts_by_match(client, monkeypatch):
    monkeypatch.setattr(scoring, "refresh_in_background", scoring.refresh)  # synchronous in the test
    data = base64.b64encode(RESUME.encode()).decode()
    profile = client.post("/api/profile/resume", json={"filename": "resume.txt", "data_base64": data}).json()
    assert profile["has_resume"] and profile["years"] == 3.3 and "Airflow" in profile["skills"]
    assert "data analyst" in profile["suggested_roles"]  # in the resume, not yet in "Your roles"
    page = client.get("/api/jobs", params={"sort": "match", "alert_roles_only": True, "hide_no_sponsor": False,
                                          "countries": "United States"}).json()["items"]
    titles = [j["title"] for j in page]
    assert titles[0] == "Data Engineer" and titles[-1] == "Registered Nurse"
    best = page[0]["match"]
    assert best["score"] >= 70 and "Airflow" in best["matched"]
    assert page[-1]["match"]["score"] < 40
    detail = client.get(f"/api/jobs/{page[0]['id']}").json()["match"]
    assert set(detail["parts"]) == {"skills", "fit", "role", "experience", "education"}
    # Editing the profile rescores: saying sponsorship isn't needed lifts the nurse job's penalty.
    nurse_before = page[-1]["match"]["score"]
    client.put("/api/profile", json={"needs_sponsorship": False})
    nurse = next(j for j in client.get("/api/jobs", params={"sort": "match", "alert_roles_only": True, "hide_no_sponsor": False,
                                                           "countries": "United States"}).json()["items"] if j["title"] == "Registered Nurse")
    assert nurse["match"]["score"] > nurse_before
    # Deleting the resume removes the scores.
    assert client.delete("/api/profile/resume").json()["has_resume"] is False
    assert client.get("/api/jobs", params={"alert_roles_only": True, "hide_no_sponsor": False}).json()["items"][0]["match"] is None


def test_unreadable_resume_is_rejected(client):
    data = base64.b64encode(b"too short").decode()
    assert client.post("/api/profile/resume", json={"filename": "r.txt", "data_base64": data}).status_code == 400


def test_resume_roles_reads_titles_held():
    resume = ("Jane Doe\nFrontend Engineer\nFrontend Developer, WebCo (2022 - 2024)\n"
              "Senior Software Engineer II | Acme\nWorked with the product manager on Power BI reports")
    assert matching.resume_roles(resume) == ["frontend engineer", "frontend developer", "software engineer"]
    assert matching.resume_roles("Registered Nurse\nRegistered Nurse, City Hospital") == ["registered nurse"]
    assert matching.resume_roles("Machine Learning Engineer\nML Engineer Intern, VisionAI") == ["machine learning engineer", "ml engineer"]
