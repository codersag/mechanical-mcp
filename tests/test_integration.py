"""End-to-end test against a real ACP install. Run with ACP_MCP_INTEGRATION=1 and a shell CDB in ACP_MCP_SHELL_CDB."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.skipif(
    not os.environ.get("ACP_MCP_INTEGRATION"), reason="needs a real ACP install"
)


def test_layup_roundtrip(tmp_path):
    import acp_mcp_server as s

    shell = os.environ["ACP_MCP_SHELL_CDB"]
    assert json.loads(s.acp_launch())["ok"]
    try:
        assert json.loads(s.acp_import_model(shell, "m"))["ok"]
        assert json.loads(
            s.acp_create_material(
                "m", "cfrp", 1.2e11, 9e9, 9e9, 0.3, 0.3, 0.4, 5e9, 5e9, 3e9, 1520.0
            )
        )["ok"]
        assert json.loads(s.acp_create_fabric("m", "ply", "cfrp", 0.125e-3))["ok"]
        assert json.loads(s.acp_create_rosette("m", "r"))["ok"]
        assert json.loads(
            s.acp_create_oriented_selection_set("m", "oss", ["All_Elements"], ["r"], [0, 0, -1])
        )["ok"]
        assert json.loads(s.acp_create_layup("m", "g", "ply", [45, 0, -45, 90], "oss"))["ok"]
        assert json.loads(s.acp_create_solid_model("m", "solid", ["oss"]))["ok"]
        upd = json.loads(s.acp_update("m"))
        assert upd["solid_models"]["solid"]["analysis_plies"] == 4
        out = tmp_path / "solid.cdb"
        assert json.loads(s.acp_export_solid_model("m", "solid", str(out)))["ok"]
        assert json.loads(s.acp_inspect_cdb(str(out)))["elements"] > 0
    finally:
        s.acp_shutdown()
