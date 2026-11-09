from app import uscis

HEADER = ["Line by line", "Fiscal Year   ", "Employer (Petitioner) Name", "Tax ID", "Industry (NAICS) Code",
          "Petitioner City", "Petitioner State", "Petitioner Zip Code", "New Employment Approval",
          "New Employment Denial", "Continuation Approval", "Continuation Denial",
          "Change with Same Employer Approval", "Change with Same Employer Denial", "New Concurrent Approval",
          "New Concurrent Denial", "Change of Employer Approval", "Change of Employer Denial", "Amended Approval",
          "Amended Denial"]


def row(year, name, new_ok, new_no, cont_ok):
    return ["1", str(year), name, "", "", "", "", "", str(new_ok), str(new_no), str(cont_ok)] + ["0"] * 9


def test_import_and_lookup(session, tmp_path):
    folder = tmp_path / "uscis"
    folder.mkdir()
    rows = [row(2026, "GOOGLE LLC", 38, 2, 46), row(2026, "GOOGLE LLC", 2, 0, "1,004"),  # two locations
            row(2025, "GOOGLE LLC", 30, 0, 40), row(2026, "GOOGLEPLEX CATERING", 5, 0, 0)]
    text = "\n".join("\t".join(r) for r in [HEADER, *rows]) + "\n"
    (folder / "Employer Information.csv").write_text(text, encoding="utf-16")
    assert uscis.import_folder(session, folder) == 3
    found = uscis.lookup(session, "Google")
    assert [y["fiscal_year"] for y in found["years"]] == [2026, 2025]
    fy26 = found["years"][0]
    assert fy26["new_approvals"] == 40 and fy26["new_denials"] == 2 and fy26["approvals"] == 40 + 1050
    assert uscis.lookup(session, "Globex") is None
