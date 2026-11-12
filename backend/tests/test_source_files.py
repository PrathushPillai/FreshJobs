"""Source files must not contain control characters. A regex written as "\b" through a tool that unescapes it
becomes a backspace (0x08): the pattern then silently never matches (2026-11-10: one US-person rule in
sponsorship.py and the title->occupation rules in h1b.py)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ALLOWED = {9, 10, 13}  # tab, newline, carriage return


def test_no_control_characters_in_source():
    files = [p for p in (ROOT / "backend").rglob("*.py") if ".venv" not in p.parts]
    files += list((ROOT / "frontend" / "src").rglob("*.ts*"))
    bad = [str(p.relative_to(ROOT)) for p in files if any(b < 32 and b not in _ALLOWED for b in p.read_bytes())]
    assert not bad, f"control characters in {bad}"


def test_server_scripts_have_unix_line_endings():
    # The laptop checks files out with Windows line endings (core.autocrlf); a shell script with them fails on
    # the server ("bash\r: No such file or directory": the nightly backup, 2026-11-11). deploy.sh also strips
    # them on the server, but the files in the repo should be right.
    files = [p for p in (ROOT / "deploy").iterdir() if p.is_file()]
    files += [p for p in ROOT.rglob("*.sh") if "node_modules" not in p.parts and ".venv" not in p.parts]
    bad = [str(p.relative_to(ROOT)) for p in files if b"\r" in p.read_bytes()]
    assert not bad, f"Windows line endings in {bad}"
