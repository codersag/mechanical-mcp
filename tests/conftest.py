import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Before any test module imports the server: its remembered port must not be the user's real one.
# The folder lives under pytest's own base temp (removed with it), never loose in %TEMP%.
_STATE = Path(tempfile.mkdtemp(prefix="mech_state_"))
os.environ["MECHANICAL_MCP_STATE_DIR"] = str(_STATE)
# Most tests use a fake model with two analyses and rely on index 0; the "explicit analysis"
# refusal has its own tests that switch it back on.
os.environ["MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS"] = "0"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_STATE, ignore_errors=True)


@pytest.fixture(autouse=True)
def allowed_roots(tmp_path_factory, monkeypatch):
    """File access is confined to an allow-list; tests may use the pytest temp area only."""
    base = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("MECHANICAL_MCP_ROOTS", str(base))
    monkeypatch.delenv("MECHANICAL_MCP_ENABLE_SCRIPT", raising=False)


@pytest.fixture
def server():
    import mechanical_mcp_server as s

    return s


@pytest.fixture
def mech(monkeypatch, server):
    """(fake model, server module): every tool runs its generated script against the fake."""
    from fake_mech import Fake

    fake = Fake()
    scripts = []

    def fake_run(script, safe_text=True):
        scripts.append(script)
        return fake.run(script, keep_markers=True)

    monkeypatch.setattr(server, "_run", fake_run)
    monkeypatch.setattr(server, "_check_connection", lambda: None)
    fake.scripts = scripts
    return fake, server


@pytest.fixture(autouse=True)
def clean_server_state():
    """Module-level bookkeeping must not leak from one test into the next."""
    import mechanical_mcp_server as srv

    srv._SOLVE_UNKNOWN.clear()
    srv._RECONNECTED.clear()
    yield
    srv._SOLVE_UNKNOWN.clear()
    srv._RECONNECTED.clear()
