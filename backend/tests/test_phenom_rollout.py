"""The Phenom careers sites go in a batch at a time, only after the last batch loaded cleanly (pipeline/phenom_rollout)."""

import json

import pytest

from app import lanes
from app.models import Company
from app.pipeline import phenom_rollout


@pytest.fixture
def sites(tmp_path, monkeypatch):
    monkeypatch.setattr(lanes, "lanes_dir", lambda: tmp_path)
    path = tmp_path / "phenom_sites.json"
    monkeypatch.setattr(phenom_rollout, "results_file", lambda platform="phenom", review=False: tmp_path / f"{platform}_sites.json")
    results = {f"Employer {i}": {"slug": f"careers.e{i}.com/us/en", "us_jobs": 1000 - i, "workday_jobs": 1000}
               for i in range(8)}
    results["Circle K"] = {"slug": "careers.circle.com/us/en", "us_jobs": 39, "workday_jobs": 6106}  # another company
    results["No site"] = {"slug": None, "workday_jobs": 500}
    path.write_text(json.dumps(results))
    return results


def test_adds_a_batch_biggest_first_then_waits_for_it_to_load(session, sites):
    first = phenom_rollout.step(session, max_add=3)
    assert first["added"] == ["Employer 0", "Employer 1", "Employer 2"] and first["left"] == 5
    again = phenom_rollout.step(session, max_add=3)
    assert again["added"] == [] and "Employer 0 not checked yet" in again["waiting_on"]
    for c in session.query(Company).filter_by(ats="phenom"):
        c.poll_count, c.fail_streak = 1, 0
    session.commit()
    assert phenom_rollout.step(session, max_add=3)["added"] == ["Employer 3", "Employer 4", "Employer 5"]


def test_a_failing_site_or_a_restarted_slow_lane_holds_the_next_batch(session, sites):
    from datetime import datetime, timezone

    phenom_rollout.step(session, max_add=1)
    c = session.query(Company).filter_by(ats="phenom").one()
    c.poll_count, c.fail_streak = 3, 2
    session.commit()
    assert phenom_rollout.step(session)["waiting_on"] == ["Employer 0 failing (2 in a row)"]
    c.fail_streak = 0
    session.commit()
    lanes.record_restart("slow", "one run going for 170 min", datetime.now(timezone.utc))
    assert phenom_rollout.step(session)["waiting_on"] == ["the slow lane was restarted in the last 12 hours"]


def test_a_site_far_smaller_than_the_employers_workday_board_is_never_added(session, sites):
    added = phenom_rollout.step(session, max_add=20, force=True)["added"]
    assert "Circle K" not in added and "No site" not in added and len(added) == 8


def test_one_site_under_two_employer_names_is_added_once(session, sites):
    import json as _json

    results = _json.loads(phenom_rollout.results_file().read_text())
    results["Mercy"] = {"slug": "careers.mercy.com/us/en", "us_jobs": 1445, "workday_jobs": 2673}
    results["Mercy Clinic Springfield Communities"] = {"slug": "careers.mercy.com/us/en", "us_jobs": 1445,
                                                       "workday_jobs": 2503}
    phenom_rollout.results_file().write_text(_json.dumps(results))
    added = phenom_rollout.step(session, max_add=20, force=True)["added"]  # no UNIQUE (ats, slug) error
    assert "Mercy" in added and "Mercy Clinic Springfield Communities" not in added


def test_radancy_sites_roll_out_and_old_boards_dont_hold_them(session, sites, tmp_path):
    import json as _json

    (tmp_path / "radancy_sites.json").write_text(_json.dumps(
        {"Big Co": {"slug": "jobs.bigco.com", "us_jobs": 900, "workday_jobs": 1000}}))
    session.add(Company(name="Old Radancy Board", ats="radancy", slug="careers.old.com", enabled=True,
                        poll_count=0, fail_streak=12))  # found by discovery long ago, failing: not this rollout's
    session.commit()
    out = phenom_rollout.step(session, platform="radancy")
    assert out["added"] == ["Big Co"] and out["waiting_on"] == []
    assert session.query(Company).filter_by(ats="radancy", slug="jobs.bigco.com").one()


def test_a_site_with_a_skip_note_is_never_added(session, sites):
    results = json.loads(phenom_rollout.results_file().read_text())
    results["Store chain"] = {"slug": "careers.store.com", "us_jobs": 5000, "workday_jobs": 600,
                              "skip": "left out by hand: mostly store jobs"}
    phenom_rollout.results_file().write_text(json.dumps(results))
    added = phenom_rollout.step(session, max_add=20, force=True)["added"]
    assert "Store chain" not in added and len(added) == 8
