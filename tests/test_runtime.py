"""Connection handling, script execution, solve, gating and file access."""

import asyncio
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fake_mech import Fake, Obj

import mcp_paths as paths


def J(s):
    return json.loads(s)


class FakeSession:
    """Stands in for a PyMechanical session: records the scripts it is sent."""

    def __init__(self, fail_with=None, on_run=None):
        self.sent = []
        self.fail_with = fail_with
        self.on_run = on_run
        self._channel = types.SimpleNamespace(
            closed=False, close=lambda: setattr(self._channel, "closed", True)
        )
        self._cleanup_on_exit = True
        self.exited = False

    def run_python_script(self, script):
        self.sent.append(script)
        if self.on_run:
            self.on_run(script)
        if self.fail_with:
            raise self.fail_with
        return ""

    def exit(self, force=False):
        self.exited = True


@pytest.fixture
def conn(server, monkeypatch):
    """The server module with a fake session installed as the connection."""
    sess = FakeSession()
    monkeypatch.setattr(server, "_mechanical", sess)
    monkeypatch.setattr(server, "_port", 12345)
    monkeypatch.setattr(
        server, "_BG", {"thread": None, "result": None, "started": 0.0, "analysis": 0}
    )
    return server, sess


# -- A3: never re-send -----------------------------------------------------------------------------


def test_a_failed_run_is_never_sent_twice(conn):
    s, sess = conn
    sess.fail_with = TimeoutError("deadline exceeded")
    out = s._run("print(1)")
    assert out.startswith("Error:") and "may or may not have run" in out
    assert len(sess.sent) == 1


def test_a_failed_run_does_not_reconnect_and_drops_the_channel(conn, monkeypatch):
    s, sess = conn
    reconnects = []
    monkeypatch.setattr(s, "_connect", lambda port: reconnects.append(port))
    sess.fail_with = RuntimeError("channel closed")
    s._run("print(1)")
    assert reconnects == [] and len(sess.sent) == 1
    assert s._mechanical is None and sess._channel.closed is True
    assert sess._cleanup_on_exit is False  # dropping the object must never exit Mechanical


def test_add_fixed_support_is_not_duplicated_by_a_timeout(conn):
    s, sess = conn
    sess.fail_with = TimeoutError("deadline exceeded")
    out = J(s.add_fixed_support("", entity_ids="1"))
    assert out["ok"] is False
    assert len(sess.sent) == 1


def test_a_solve_that_times_out_is_not_sent_twice(conn):
    s, sess = conn
    sess.fail_with = TimeoutError("deadline exceeded")
    s.solve_analysis(0)
    assert len(sess.sent) == 1


def test_run_without_connection(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", None)
    assert "not connected" in server._run("print(1)")


def test_busy_lock_gives_up_instead_of_hanging(conn, monkeypatch):
    s, sess = conn
    monkeypatch.setattr(s, "LOCK_WAIT_S", 0.05)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with s._MECH_LOCK:
            held.set()
            release.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(1)
    try:
        assert "busy" in s._run("print(1)")
        assert s._check_connection() is None  # connected: only the run has to wait
    finally:
        release.set()
        t.join()
    assert sess.sent == []


def test_wrapper_runs_generated_scripts_in_a_private_namespace(server):
    w = server._wrapper("/tmp/a.py", "/tmp/o.txt", True)
    assert "dict(globals())" in w and "exec(_code, _scope)" in w
    assert "globals()['str']=_safestr" in w and "ensure_ascii" in w
    own = server._wrapper("/tmp/a.py", "/tmp/o.txt", False)
    assert "_scope=globals()" in own and "_safestr" not in own
    compile(w, "wrapper", "exec")
    compile(own, "wrapper", "exec")


def test_execute_reads_back_and_cleans_up(server, tmp_path):
    seen = {}

    def on_run(wrapper):
        # the wrapper embeds raw paths: pull the output path and write a result there
        import re

        paths = re.findall(r'u"([^"]+)"', wrapper)
        seen["paths"] = paths
        Path(paths[1]).write_text("hello \u00e9", encoding="utf-8")

    sess = FakeSession(on_run=on_run)
    assert server._execute(sess, "print(1)") == "hello \u00e9"
    assert all(not Path(p).exists() for p in seen["paths"])


# -- connection management ----------------------------------------------------------------------------


def test_state_file_is_per_user_not_the_shared_temp_folder(server):
    shared = Path(__import__("tempfile").gettempdir()) / "mechanical_mcp_state.json"
    assert server._STATE_FILE != shared
    assert server._STATE_FILE.name == "mechanical_port.json"
    assert "mechanical-mcp" in str(server._state_dir()) or os.environ.get(
        "MECHANICAL_MCP_STATE_DIR"
    )


def test_state_dir_override(server, monkeypatch, tmp_path):
    monkeypatch.setenv("MECHANICAL_MCP_STATE_DIR", str(tmp_path / "x"))
    assert server._state_dir() == tmp_path / "x"


def test_remembered_port_roundtrip_and_validation(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "st" / "p.json")
    assert server._last_port() == 0
    server._remember_port(4321)
    assert server._last_port() == 4321
    server._STATE_FILE.write_text(json.dumps({"port": 99999999}))
    assert server._last_port() == 0
    server._STATE_FILE.write_text("not json")
    assert server._last_port() == 0
    server._forget_port()
    assert not server._STATE_FILE.exists()


def test_dead_port_is_refused_before_any_connection_attempt(server, monkeypatch):
    called = []
    fake_mod = types.ModuleType("ansys.mechanical.core")
    fake_mod.connect_to_mechanical = lambda port, **kwargs: called.append(port)
    monkeypatch.setitem(sys.modules, "ansys", types.ModuleType("ansys"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical", types.ModuleType("ansys.mechanical"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical.core", fake_mod)
    monkeypatch.setattr(server, "_port_open", lambda p: False)
    with pytest.raises(ConnectionError):
        server._connect(50999)
    assert called == []
    out = J(server.connect_to_mechanical(50999))
    assert out["ok"] is False and "Nothing is listening" in out["error"]


def _install_fake_core(monkeypatch, make):
    fake_mod = types.ModuleType("ansys.mechanical.core")
    fake_mod.connect_to_mechanical = lambda port, **kwargs: make(port)
    monkeypatch.setitem(sys.modules, "ansys", types.ModuleType("ansys"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical", types.ModuleType("ansys.mechanical"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical.core", fake_mod)


def test_a_failed_reconnect_keeps_the_working_connection(conn, monkeypatch, tmp_path):
    s, old = conn
    monkeypatch.setattr(s, "_STATE_FILE", tmp_path / "p.json")
    monkeypatch.setattr(s, "_port_open", lambda p: True)
    bad = FakeSession(fail_with=RuntimeError("no answer"))
    _install_fake_core(monkeypatch, lambda port: bad)
    out = J(s.connect_to_mechanical(2222))
    assert out["ok"] is False
    assert s._mechanical is old and s._port == 12345 and old._channel.closed is False
    assert bad._channel.closed is True  # the half-open attempt is dropped
    assert not s._STATE_FILE.exists()  # and its port is not remembered


def test_connect_remembers_the_port_only_after_the_check_succeeds(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "p.json")
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "_mechanical", None)

    class Good(FakeSession):
        def run_python_script(self, script):
            import re

            paths = re.findall(r'u"([^"]+)"', script)
            Path(paths[1]).write_text(
                json.dumps({"ok": True, "analyses": [{"index": 0, "name": "A", "type": "Static"}]}),
                encoding="utf-8",
            )

    _install_fake_core(monkeypatch, lambda port: Good())
    out = J(server.connect_to_mechanical(3333))
    assert out["ok"] is True and out["port"] == 3333
    assert out["analyses"] == [{"index": 0, "name": "A", "type": "Static"}]
    assert server._last_port() == 3333 and server._port == 3333
    server._close_channel()


def test_disconnect_leaves_mechanical_running(conn, monkeypatch, tmp_path):
    s, sess = conn
    monkeypatch.setattr(s, "_STATE_FILE", tmp_path / "p.json")
    out = J(s.disconnect_from_mechanical())
    assert out["ok"] is True and sess.exited is False and s._mechanical is None


def test_shutdown_needs_the_exact_confirmation(conn):
    s, sess = conn
    for confirm in ("", "yes", "shut down mechanical", "SHUT DOWN"):
        out = J(s.disconnect_from_mechanical(True, confirm))
        assert out["ok"] is False and sess.exited is False and s._mechanical is sess
    out = J(s.disconnect_from_mechanical(True, "SHUT DOWN MECHANICAL"))
    assert out["ok"] is True and sess.exited is True and s._mechanical is None


# -- run_mechanical_script gate ------------------------------------------------------------------------


def test_run_mechanical_script_is_off_by_default(conn):
    s, sess = conn
    out = J(s.run_mechanical_script("print(1)"))
    assert out["ok"] is False and "MECHANICAL_MCP_ENABLE_SCRIPT=1" in out["error"]
    assert sess.sent == []


@pytest.mark.parametrize("value", ["0", "true", "yes", " 1", "11"])
def test_only_exactly_one_enables_it(conn, monkeypatch, value):
    s, sess = conn
    monkeypatch.setenv("MECHANICAL_MCP_ENABLE_SCRIPT", value)
    assert J(s.run_mechanical_script("print(1)"))["ok"] is False
    assert sess.sent == []


def test_run_mechanical_script_when_enabled_uses_stock_semantics(conn, monkeypatch):
    s, sess = conn
    monkeypatch.setenv("MECHANICAL_MCP_ENABLE_SCRIPT", "1")
    s.run_mechanical_script("print(1)")
    assert len(sess.sent) == 1 and "_scope=globals()" in sess.sent[0]


# -- solve ---------------------------------------------------------------------------------------------


def test_solve_ok_is_tied_to_status_done(mech):
    fake, s = mech
    fake.static.Solution.solve_result = "Done"
    out = J(s.solve_analysis(0))
    assert out["ok"] is True and out["status"] == "Done" and out["solved"] is True
    fake.static.Solution.solve_result = "SolveRequired"
    out = J(s.solve_analysis(0))
    assert out["ok"] is False and out["status"] == "SolveRequired"
    fake.static.Solution.solve_result = "Failed"
    assert J(s.solve_analysis(0))["ok"] is False


def test_solve_error_is_reported_and_ok_is_false(mech):
    fake, s = mech
    fake.static.Solution.solve_raises = RuntimeError("solver crashed")
    out = J(s.solve_analysis(0))
    assert out["ok"] is False and out["error"] == "solver crashed"


def test_solve_does_not_clear_the_messages_pane_and_reports_only_new_ones(mech):
    fake, s = mech
    fake.messages.extend([Obj("m", Severity="Info", DisplayString="old message")])

    def solve(wait):
        fake.messages.append(Obj("m", Severity="Error", DisplayString="solver says no"))
        fake.messages.append(
            Obj("m", Severity="Info", DisplayString="Started the grpc server on port 1")
        )
        fake.static.Solution.Status = "Done"

    fake.static.Solution.Solve = solve
    out = J(s.solve_analysis(0))
    assert fake.messages.cleared == 0 and len(fake.messages) == 3
    assert "Messages.Clear" not in fake.scripts[-1]
    assert out["messages"] == ["Error: solver says no"] and out["error_messages"] == [
        "Error: solver says no"
    ]


def test_solve_always_blocks_inside_mechanical(mech):
    fake, s = mech
    s.solve_analysis(0, wait=True)
    assert "Solve(True)" in fake.scripts[-1] and "Solve(False)" not in fake.scripts[-1]


def test_wait_false_runs_on_a_background_thread_and_status_reports_solving(mech, monkeypatch):
    fake, s = mech
    gate = threading.Event()
    started = threading.Event()

    def solve(wait):
        assert wait is True  # Mechanical's local solve refuses wait=False
        started.set()
        gate.wait(5)
        fake.static.Solution.Status = "Done"

    fake.static.Solution.Solve = solve
    monkeypatch.setattr(s, "_BG", {"thread": None, "result": None, "started": 0.0, "analysis": 0})
    out = J(s.solve_analysis(0, wait=False))
    assert out["ok"] is True and out["started"] is True and out["status"] == "Solving"
    assert started.wait(2)
    st = J(s.get_solve_status(0))
    assert st["status"] == "Solving" and st["in_background"] is True and st["solved"] is False
    assert J(s.solve_analysis(0, wait=False))["ok"] is False  # one at a time
    gate.set()
    s._BG["thread"].join(5)
    st = J(s.get_solve_status(0))
    assert st["status"] == "Done" and st["last_solve"]["ok"] is True
    assert "last_solve" not in J(s.get_solve_status(0))  # delivered once


def test_status_filters_the_grpc_notice_and_flags_solve_required(mech):
    fake, s = mech
    fake.messages.extend(
        [
            Obj("m", Severity="Info", DisplayString="Started the grpc server on port 5"),
            Obj("m", Severity="Warning", DisplayString="mesh quality"),
        ]
    )
    out = J(s.get_solve_status(0))
    assert (
        out["status"] == "SolveRequired"
        and out["solve_required"] is True
        and out["solved"] is False
    )
    assert out["messages"] == ["Warning: mesh quality"]


# -- tools run on worker threads -------------------------------------------------------------------------


def test_all_50_tools_are_registered(server):
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 50
    assert {t.name for t in tools} >= {
        "add_standard_gravity",
        "run_mechanical_script",
        "linearize_path",
    }


def test_tools_do_not_block_the_event_loop(server, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_run(script, safe_text=True):
        started.set()
        release.wait(3)
        return json.dumps({"ok": True, "materials": []})

    monkeypatch.setattr(server, "_run", slow_run)
    monkeypatch.setattr(server, "_check_connection", lambda: None)

    async def main():
        slow = asyncio.create_task(server.mcp.call_tool("list_materials", {}))
        await asyncio.sleep(0.2)
        assert started.is_set()
        t0 = time.time()
        await asyncio.sleep(0.05)  # the loop is free while the tool waits
        assert time.time() - t0 < 1
        release.set()
        return await slow

    res = asyncio.run(main())
    assert "materials" in str(res)


def test_validation_errors_in_registered_tools_become_json(server):
    res = asyncio.run(server.mcp.call_tool("set_mesh_element_size", {"element_size_mm": -5}))
    assert "element_size_mm must be at least" in str(res)


# -- file access -------------------------------------------------------------------------------------------


def test_allow_accepts_a_fresh_json_inside_the_root(server, tmp_path):
    p, err = server._allow(tmp_path / "a.json", exts={".json"}, write=True)
    assert err is None and p == (tmp_path / "a.json").resolve()


@pytest.mark.parametrize(
    "raw",
    [
        "\\\\host\\share\\a.json",
        "//host/share/a.json",
        "/\\host/a.json",
        "\\\\?\\C:\\a.json",
        "\\\\.\\pipe\\x",
    ],
)
def test_network_paths_are_refused_from_the_text(server, raw):
    p, err = server._allow(raw, exts={".json"}, write=True)
    assert p is None and "Network" in json.loads(err)["error"]


def test_outside_the_roots_is_refused(server, tmp_path):
    outside = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Temp" / "x.json"
    p, err = server._allow(outside, exts={".json"}, write=True)
    assert p is None and "outside" in json.loads(err)["error"]
    p, err = server._allow(tmp_path / ".." / ".." / "x.json", exts={".json"}, write=True)
    assert p is None


def test_alternate_data_streams_are_refused(server, tmp_path):
    p, err = server._allow(str(tmp_path / "a.json:evil"), exts={".json"}, write=True)
    assert p is None and "':'" in json.loads(err)["error"]


def test_extension_and_control_characters(server, tmp_path):
    for name in ("a.exe", "a.py", "a.json.bat", "a"):
        assert server._allow(tmp_path / name, exts={".json"}, write=True)[0] is None
    assert server._allow(str(tmp_path / "a\x00.json"), exts={".json"}, write=True)[0] is None
    assert server._allow("", exts={".json"}, write=True)[0] is None


def test_overwrite_needs_the_flag(server, tmp_path):
    f = tmp_path / "a.json"
    f.write_text("{}")
    p, err = server._allow(f, exts={".json"}, write=True)
    assert p is None and "overwrite=true" in json.loads(err)["error"]
    p, err = server._allow(f, exts={".json"}, write=True, overwrite=True)
    assert err is None


def test_missing_folder_and_default_root(server, tmp_path, monkeypatch):
    assert server._allow(tmp_path / "nope" / "a.json", exts={".json"}, write=True)[0] is None
    monkeypatch.delenv("MECHANICAL_MCP_ROOTS")
    monkeypatch.chdir(tmp_path)
    assert server._roots() == [tmp_path.resolve()] or server._too_broad(tmp_path.resolve())
    monkeypatch.chdir(Path.home())
    assert server._roots() == []  # the home folder is too broad to be a default root


def test_an_explicit_empty_allow_list_denies_everything(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MECHANICAL_MCP_ROOTS", "\\\\host\\share")
    assert server._roots() == []
    assert server._allow(tmp_path / "a.json", exts={".json"}, write=True)[0] is None


def test_reading_requires_an_existing_regular_file(server, tmp_path):
    assert server._allow(tmp_path / "missing.json", exts={".json"})[0] is None
    assert server._allow(tmp_path, exts={".json"})[0] is None
    f = tmp_path / "ok.json"
    f.write_text("{}")
    assert server._allow(f, exts={".json"})[0] == f.resolve()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_destinations_are_refused(server, tmp_path):
    target = tmp_path / "t.json"
    target.write_text("{}")
    link = tmp_path / "l.json"
    link.symlink_to(target)
    assert server._allow(link, exts={".json"}, write=True, overwrite=True)[0] is None


def test_export_validates_the_path_before_running_anything(mech, tmp_path):
    fake, s = mech
    for path in (
        str(tmp_path / "x.txt"),
        "\\\\h\\s\\x.json",
        "C:/Windows/x.json",
        str(tmp_path / "x.json:s"),
    ):
        assert J(s.export_nodal_results("Normal Stress", path, 0))["ok"] is False
    assert fake.scripts == []
    (tmp_path / "exists.json").write_text("{}")
    assert (
        J(s.export_nodal_results("Normal Stress", str(tmp_path / "exists.json"), 0))["ok"] is False
    )
    assert fake.scripts == []


def _solved_export_fixture(fake):
    fake.static.Solution.Status = "Done"
    res = fake.static.Solution.AddNormalStress()
    res.Name = "Normal Stress"
    res.PlotData = {"Node": [1, 2, 3], "Values": [10.0, 20.0, 30.0]}
    res.EvaluateAllResults = lambda: None
    nd = {i: Obj("n", X=0.001 * i, Y=0.0, Z=0.002) for i in (1, 2, 3)}
    fake.ext.DataModel.MeshDataByName = lambda n: Obj("md", Unit="m", NodeById=lambda i: nd[i])
    return res


def test_export_writes_units_and_values(mech, tmp_path):
    fake, s = mech
    _solved_export_fixture(fake)
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))
    assert (
        out["ok"] is True
        and out["coordinate_unit"] == "mm"
        and out["value_unit"] == "Pa"
        and out["signed"] is True
    )
    doc = json.loads((tmp_path / "n.json").read_text())
    assert doc["coordinate_unit"] == "mm" and doc["value_unit"] == "Pa"
    assert doc["xyz_mm"][0] == [1.0, 0.0, 2.0] and doc["values"] == [10.0, 20.0, 30.0]
    assert not list(tmp_path.glob("*.part"))


def test_export_converts_mesh_units(mech, tmp_path):
    fake, s = mech
    _solved_export_fixture(fake)
    nd = {i: Obj("n", X=float(i), Y=0.0, Z=0.0) for i in (1, 2, 3)}
    fake.ext.DataModel.MeshDataByName = lambda n: Obj("md", Unit="mm", NodeById=lambda i: nd[i])
    assert J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))["ok"] is True
    assert json.loads((tmp_path / "n.json").read_text())["xyz_mm"][1] == [2.0, 0.0, 0.0]


def test_export_refuses_an_unsolved_model_clearly(mech, tmp_path):
    fake, s = mech
    res = fake.static.Solution.AddNormalStress()
    res.Name = "Normal Stress"
    res.PlotData = None
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))
    assert (
        out["ok"] is False
        and "not solved yet" in out["error"]
        and not (tmp_path / "n.json").exists()
    )


def test_export_marks_unsigned_quantities(mech, tmp_path):
    fake, s = mech
    res = _solved_export_fixture(fake)
    res._type = "EquivalentStress"
    res.Name = "Equivalent Stress"
    out = J(s.export_nodal_results("Equivalent Stress", str(tmp_path / "e.json"), 0))
    assert out["ok"] is True and out["signed"] is False


def test_export_failure_leaves_no_partial_file(mech, tmp_path):
    fake, s = mech
    res = _solved_export_fixture(fake)
    res.PlotData = {"Node": [1, 2, 3], "Values": ["x", 2, 3]}
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))
    assert out["ok"] is False and list(tmp_path.iterdir()) == []


# -- reports -------------------------------------------------------------------------------------------------


def _report_fake(fake):
    fake.add_body("Plate")
    fake.static.Solution.Status = "Done"
    fake.static.AddFixedSupport()
    r = fake.static.Solution.AddEquivalentStress()
    r.Name = "Equivalent Stress"


def test_report_validates_format_and_extension_before_connecting(mech, tmp_path):
    fake, s = mech
    for fmt, name in (
        ("pdf", "r.pdf"),
        ("PDF", "r.txt"),
        ("", "r.txt"),
        ("docx", "r.txt"),
        ("txt", "r.docx"),
        ("txt", "r"),
    ):
        out = J(s.generate_report(str(tmp_path / name), 0, fmt))
        assert out["ok"] is False, (fmt, name)
    assert fake.scripts == [] and list(tmp_path.iterdir()) == []


def test_txt_report_keeps_the_requested_path(mech, tmp_path):
    fake, s = mech
    _report_fake(fake)
    out = J(s.generate_report(str(tmp_path / "r.txt"), 0, "TXT"))
    assert out["ok"] is True and out["report_path"] == paths.redact(
        str((tmp_path / "r.txt").resolve())
    )
    text = (tmp_path / "r.txt").read_text(encoding="utf-8")
    assert (
        "Plate | Structural Steel" in text
        and "Fixed Support" in text
        and "Equivalent Stress" in text
    )
    assert (
        J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt"))["ok"] is False
    )  # no silent overwrite
    assert J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt", True))["ok"] is True
    assert not list(tmp_path.glob("*.part"))


def test_report_bad_analysis_index_is_a_clear_error(mech, tmp_path):
    fake, s = mech
    out = J(s.generate_report(str(tmp_path / "r.txt"), 5, "txt"))
    assert out["ok"] is False and "out of range" in out["error"] and "raw" not in out
    assert list(tmp_path.iterdir()) == []


def test_report_with_noise_before_the_json(server, tmp_path, monkeypatch):
    """json.loads(raw) used to fail on any ACT log line before the data."""
    fake = Fake()
    _report_fake(fake)
    monkeypatch.setattr(server, "_check_connection", lambda: None)
    monkeypatch.setattr(
        server, "_run", lambda sc, safe_text=True: "Info: some ACT log line\n" + fake.run(sc)
    )
    assert J(server.generate_report(str(tmp_path / "r.txt"), 0, "txt"))["ok"] is True


def test_docx_dependency_is_checked_before_any_work(mech, tmp_path, monkeypatch):
    fake, s = mech
    monkeypatch.setitem(sys.modules, "docx", None)  # import raises ImportError
    out = J(s.generate_report(str(tmp_path / "r.docx"), 0, "docx"))
    assert out["ok"] is False and "python-docx" in out["error"] and "[report]" in out["error"]
    assert fake.scripts == []


def test_docx_report_with_control_characters_in_names(mech, tmp_path, monkeypatch):
    fake, s = mech
    _report_fake(fake)
    fake.model.Geometry.Bodies[0].Name = "Pl\u0001ate\u0008"
    written = {}

    class Cell:
        text = ""

    class Row:
        def __init__(self):
            self.cells = [Cell(), Cell(), Cell(), Cell()]

    class Table:
        style = None

        def __init__(self, rows, cols):
            self.rows = [Row() for _ in range(rows)]

        def add_row(self):
            r = Row()
            self.rows.append(r)
            return r

    class Document:
        def __init__(self):
            self.texts = written.setdefault("texts", [])

        def add_heading(self, t, level=1):
            self.texts.append(t)
            return types.SimpleNamespace(alignment=None)

        def add_paragraph(self, t, style=None):
            self.texts.append(t)

        def add_table(self, rows, cols):
            t = Table(rows, cols)
            written.setdefault("tables", []).append(t)
            return t

        def save(self, path):
            Path(path).write_bytes(b"docx")

    docx = types.ModuleType("docx")
    docx.Document = Document
    enum = types.ModuleType("docx.enum")
    text = types.ModuleType("docx.enum.text")
    text.WD_ALIGN_PARAGRAPH = types.SimpleNamespace(CENTER=1)
    monkeypatch.setitem(sys.modules, "docx", docx)
    monkeypatch.setitem(sys.modules, "docx.enum", enum)
    monkeypatch.setitem(sys.modules, "docx.enum.text", text)
    out = J(s.generate_report(str(tmp_path / "r.docx"), 0, "docx"))
    assert out["ok"] is True and (tmp_path / "r.docx").read_bytes() == b"docx"
    body_row = written["tables"][0].rows[1]
    assert body_row.cells[0].text == "Pl?ate?"  # control characters cannot reach python-docx
    assert not list(tmp_path.glob("*.part"))


def test_report_does_not_claim_values_for_an_unsolved_model(mech, tmp_path):
    fake, s = mech
    fake.static.Solution.AddEquivalentStress().Name = "Equivalent Stress"
    assert J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt"))["ok"] is True
    text = (tmp_path / "r.txt").read_text()
    assert "Max: -" in text and "12.5" not in text


def test_background_solve_runs_through_the_real_connection_check(conn):
    """The solve thread must not be turned away by the 'busy' flag it sets itself."""
    import re

    s, sess = conn
    gate = threading.Event()

    def on_run(wrapper):
        gate.wait(5)
        paths = re.findall(r'u"([^"]+)"', wrapper)
        Path(paths[1]).write_text(
            json.dumps({"ok": True, "status": "Done", "solved": True, "messages": []}),
            encoding="utf-8",
        )

    sess.on_run = on_run
    out = J(s.solve_analysis(0, wait=False))
    assert out["started"] is True
    # everything else is told Mechanical is busy straight away, even while the solve holds the lock
    t0 = time.time()
    assert "busy" in J(s.list_materials())["error"]
    assert "busy" in J(s.connect_to_mechanical(4000))["error"]
    assert "busy" in J(s.disconnect_from_mechanical())["error"]
    assert time.time() - t0 < 2
    st = J(s.check_mechanical_connection())
    assert st["connected"] is True and st["busy"] is True
    gate.set()
    s._BG["thread"].join(5)
    assert len(sess.sent) == 1  # the solve script was sent exactly once
    st = J(s.get_solve_status(0))
    assert st["last_solve"]["ok"] is True and st["last_solve"]["status"] == "Done"
