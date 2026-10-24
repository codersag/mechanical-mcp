"""Privacy: nothing that identifies the person running the server leaves it or is stored."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import mcp_paths as paths

BS = chr(92)
REPO = Path(__file__).resolve().parent.parent


def _fake_profile(monkeypatch, tmp_path):
    home = tmp_path / "Users" / "alexsample"
    home.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("USERNAME", "alexsample")
    monkeypatch.setenv("COMPUTERNAME", "WORKSTATION42")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_redact_removes_profile_user_host_licence_and_email(monkeypatch, tmp_path):
    home = _fake_profile(monkeypatch, tmp_path)
    text = (
        "file %s"
        + BS
        + "proj"
        + BS
        + "a.mechdb as alexsample on WORKSTATION42; server 1055@licbox.example.com; "
        "mail alex.s@example.org; Customer ID: 123456; hostname: WORKSTATION42; -acl 0123456789abcdef"
    ) % home
    out = paths.redact(text)
    for leaked in (
        "alexsample",
        "WORKSTATION42",
        "licbox",
        "example.org",
        "123456",
        "0123456789abcdef",
    ):
        assert leaked not in out
    assert "%USERPROFILE%" in out


def test_redact_handles_json_escaped_and_forward_slash_paths(monkeypatch, tmp_path):
    home = _fake_profile(monkeypatch, tmp_path)
    for variant in (str(home), str(home).replace("\\", "/"), str(home).replace("\\", "\\\\")):
        assert "alexsample" not in paths.redact("x " + variant + "/z")


def test_redaction_can_be_switched_off(monkeypatch, tmp_path):
    _fake_profile(monkeypatch, tmp_path)
    monkeypatch.setenv("MECHANICAL_MCP_REDACT", "0")
    assert paths.redact("alexsample") == "alexsample"


def test_project_identity_is_hash_and_folder_name_only(monkeypatch, tmp_path):
    home = _fake_profile(monkeypatch, tmp_path)
    ident = paths.project_identity(str(home / "Projects" / "bracket" / "SYS.mechdb"))
    blob = json.dumps(ident)
    assert "alexsample" not in blob and "Users" not in blob
    assert re.fullmatch(r"[0-9a-f]{16}", ident["id"])
    assert ident["name"] == "SYS.mechdb"
    same = paths.project_identity(str(home / "projects" / "BRACKET" / "sys.mechdb") + "\\")
    assert same["id"] == ident["id"]  # case and trailing separator do not matter


def test_state_file_holds_no_path(monkeypatch, tmp_path):
    import mechanical_mcp_server as s

    home = _fake_profile(monkeypatch, tmp_path)
    s._remember_port(10000, str(home / "Work" / "SYS.mechdb"))
    raw = s._STATE_FILE.read_text(encoding="utf-8")
    try:
        assert "alexsample" not in raw and "Users" not in raw and "Work" not in raw
        st = s._last_state()
        assert st["port"] == 10000 and st["name"] == "SYS.mechdb" and len(st["project"]) == 16
    finally:
        s._forget_port()


def test_denied_path_message_hides_profile_and_roots(monkeypatch, tmp_path):
    home = _fake_profile(monkeypatch, tmp_path)
    p, err = paths.allow(str(home / "secret.txt"), exts={".txt"})
    assert p is None
    assert "alexsample" not in err and str(tmp_path) not in err.replace("%USERPROFILE%", "")


@pytest.mark.parametrize(
    "bad",
    [
        BS * 2 + "server" + BS + "share" + BS + "a.txt",
        "//server/share/a.txt",
        "C:" + BS + "x" + BS + "a.txt:stream",
        "C:" + BS + "x" + BS + "aux.txt",
        "C:" + BS + "x" + BS + "COM1.json",
        "C:" + BS + "x" + BS + "a" + chr(0) + ".txt",
    ],
)
def test_unsafe_paths_refused(bad):
    p, err = paths.allow(bad)
    assert p is None and err


def test_no_personal_data_in_tracked_text_files():
    """The files that get published contain no profile path, e-mail address or licence id."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    email = re.compile(r"[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[\w.]+")
    profile = re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+(?!<|%|\{|Jos)[\w.-]+")
    lic = re.compile(r"\b\d{4}@[A-Za-z][\w.-]+")
    offenders = []
    for rel in tracked + untracked:
        f = REPO / rel
        if f.suffix.lower() not in {".py", ".md", ".toml", ".txt", ".yml", ".yaml", ".json", ""}:
            continue
        if not f.is_file() or f.name == Path(__file__).name:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for rx in (email, profile, lic):
            if rx.search(text):
                offenders.append((rel, rx.pattern[:20]))
    assert not offenders, offenders


def test_gitignore_covers_project_and_cache_files():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for needle in ("*.mechdb", "*.wbpj", ".env", "__pycache__/", ".ruff_cache/", "*.log"):
        assert needle in gi


def test_tool_reply_is_redacted(monkeypatch, tmp_path):
    import mechanical_mcp_server as s

    home = _fake_profile(monkeypatch, tmp_path)
    out = s._err("bad %s" % home + BS + "x") if hasattr(s, "_err") else ""
    assert "alexsample" not in str(out) and os.environ["USERNAME"] == "alexsample"


def test_denial_messages_are_redacted(monkeypatch, tmp_path):
    import mechanical_mcp_server as s

    home = _fake_profile(monkeypatch, tmp_path)
    out = s._deny("cannot use " + str(home) + BS + "alexsample" + BS + "x")
    assert "alexsample" not in out and "WORKSTATION42" not in out
