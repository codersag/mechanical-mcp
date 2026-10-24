"""The ACP server confines every file argument to the allow-list and never overwrites silently."""

import json

import pytest

import acp_mcp_server as a


def J(s):
    return json.loads(s)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_MCP_ROOTS", str(tmp_path))
    return tmp_path


def test_inspect_refuses_outside_and_network_paths(roots, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "m.cdb"
    outside.write_text("x")
    out = J(a.acp_inspect_cdb(str(outside)))
    assert out["ok"] is False and "outside" in out["error"]
    out = J(a.acp_inspect_cdb("//server/share/m.cdb"))
    assert out["ok"] is False and "Network" in out["error"]


def test_inspect_rejects_wrong_extension(roots):
    f = roots / "m.exe"
    f.write_text("x")
    assert J(a.acp_inspect_cdb(str(f)))["ok"] is False


def test_prepare_refuses_existing_destination_without_overwrite(roots):
    src, dst = roots / "in.cdb", roots / "out.cdb"
    src.write_text("x")
    dst.write_text("keep")
    out = J(a.acp_prepare_external_model(str(src), str(dst)))
    assert out["ok"] is False and "overwrite" in out["error"]
    assert dst.read_text() == "keep"


def test_wb_build_script_refuses_outside_and_existing(roots, tmp_path_factory):
    outside = tmp_path_factory.mktemp("o2") / "b.py"
    assert J(a.acp_wb_build_script(str(outside), "mat", [0.0]))["ok"] is False
    inside = roots / "b.py"
    out = J(a.acp_wb_build_script(str(inside), "mat", [0.0, 90.0]))
    assert out["ok"] is True and inside.exists()
    again = J(a.acp_wb_build_script(str(inside), "mat", [0.0]))
    assert again["ok"] is False and "overwrite" in again["error"]
    assert J(a.acp_wb_build_script(str(inside), "mat", [0.0], overwrite=True))["ok"] is True


def test_wb_commands_reads_only_allowed_scripts(roots, tmp_path_factory):
    outside = tmp_path_factory.mktemp("o3") / "b.py"
    outside.write_text("x")
    assert J(a.acp_wb_commands(str(outside)))["ok"] is False
    ok = roots / "s.py"
    ok.write_text("x")
    assert J(a.acp_wb_commands(str(ok)))["ok"] is True


def test_error_replies_are_redacted(roots, monkeypatch):
    monkeypatch.setenv("USERNAME", "alexsample")
    assert "alexsample" not in a._err("failed for alexsample")
