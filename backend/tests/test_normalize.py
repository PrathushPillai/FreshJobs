

def test_clean_title():
    from app.pipeline.normalize import clean_title

    assert clean_title("(USA) Produce Lead") == "Produce Lead"
    assert clean_title(":Line Cook") == "Line Cook"
    assert clean_title("- Admissions Counselor") == "Admissions Counselor"
    assert clean_title(".NET Developer") == ".NET Developer"
    assert clean_title("C++  Developer") == "C++ Developer"
    assert clean_title("---") == "---"  # never empty
