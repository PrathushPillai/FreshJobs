import pytest

from app.pipeline.sponsorship import mentions_everify, student_visa_signal


@pytest.mark.parametrize("text, expected", [
    ("Open to F-1 OPT and STEM OPT candidates.", "welcome"),
    ("Visa sponsorship available if you hold a valid visa (H1B, OPT EAD, CPT).", "welcome"),
    ("We're happy to sponsor international candidates (e.g., STEM OPT, OPT, H1B, O1).", "welcome"),
    ("This role is not eligible for sponsorship (e.g., H-1B, F-1 OPT/STEM OPT, TN).", "excluded"),
    ("Truist will not sponsor an applicant for work visa status. This includes: H-1B, F-1 OPT, F-1 STEM OPT.",
     "excluded"),
    ("Please note: student visas, CPT, OPT, H-1B are considered restricted forms of work authorization.", "excluded"),
    ("We can't sponsor new H-1B visas. We're happy to consider candidates on F-1 status (including CPT or OPT).",
     "welcome"),
    ("Candidates on OPT or CPT will not be considered.", "excluded"),
    ("This role is not eligible for STEM OPT.", "excluded"),
    ("We are unable to accept candidates on F-1 visas.", "excluded"),
    ("You can opt in to our newsletter. Opt-out anytime.", None),
    ("Great benefits and a 401k.", None),
    ("Experience with CPT and ICD-10 coding required.", None),
    ("Model quality measured by F1 score.", None),
    ("Drive system optimization and performance tuning.", None),
    ("CPT students are welcome for this internship.", "welcome"),
    ("", None),
])
def test_student_visa_signal(text, expected):
    assert student_visa_signal(text) == expected


def test_mentions_everify():
    assert mentions_everify("Acme participates in E-Verify.")
    assert mentions_everify("We are an EVerify employer")
    assert not mentions_everify("We do not participate in E-Verify.")
    assert not mentions_everify("Verify your email")
