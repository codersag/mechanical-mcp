"""Optional live check, skipped unless MECHANICAL_MCP_LIVE_PORT names a disposable Mechanical session.

Never point this at a session with work you care about: it only reads, but it drops and re-makes
the connection."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PORT = os.environ.get("MECHANICAL_MCP_LIVE_PORT")
pytestmark = pytest.mark.skipif(
    not PORT, reason="needs a live Mechanical (MECHANICAL_MCP_LIVE_PORT)"
)

SNIPPET = """
import sys
sys.path.insert(0, {root!r})
import mechanical_mcp_server as s
print(s.connect_to_mechanical({port}))
s._mechanical.run_python_script = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated gRPC failure"))
print(s.get_mesh_statistics())
print("mech after failure:", s._mechanical)
if {again}:
    print(s.get_mesh_statistics())
"""


@pytest.mark.parametrize("again", [False, True])
def test_a_transport_failure_does_not_crash_the_process(again, tmp_path):
    root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, MECHANICAL_MCP_STATE_DIR=str(tmp_path), PYTHONUTF8="1")
    for _ in range(3):  # the crash was intermittent (2 of 3 runs)
        r = subprocess.run(
            [sys.executable, "-c", SNIPPET.format(root=root, port=int(PORT), again=again)],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "connection dropped" in r.stdout
