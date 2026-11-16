

def test_clean_title():
    from app.pipeline.normalize import clean_title

    assert clean_title("(USA) Produce Lead") == "Produce Lead"
    assert clean_title(":Line Cook") == "Line Cook"
    assert clean_title("- Admissions Counselor") == "Admissions Counselor"
    assert clean_title(".NET Developer") == ".NET Developer"
    assert clean_title("C++  Developer") == "C++ Developer"
    assert clean_title("---") == "---"  # never empty


def test_mojibake_titles_are_repaired():
    from app.pipeline.normalize import clean_title

    assert clean_title("Python Data Analyst â€“ Entry Level") == "Python Data Analyst – Entry Level"
    assert clean_title("CafÃ© Manager") == "Café Manager"
    assert clean_title("Café Manager – Lead") == "Café Manager – Lead"  # already right: unchanged
