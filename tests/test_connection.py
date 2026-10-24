"""Connection layer: finalizer ordering, handshake timeout, busy states, reconnect policy."""

import json
import socket
import threading
import time

import pytest
from helpers import Finalizing, info_reply, install_fake_core

import mcp_paths as paths


def J(s):
    return json.loads(s)


@pytest.fixture(autouse=True)
def reset(server, monkeypatch, tmp_path):
    Finalizing.events = []
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "state" / "p.json")
    monkeypatch.setattr(server, "_mechanical", None)
    monkeypatch.setattr(server, "_port", 0)
    monkeypatch.setattr(server, "_RECONNECT_FAILS", 0)
    server._RECONNECTED.clear()
    monkeypatch.setattr(
        server,
        "_BG",
        {"thread": None, "result": None, "started": 0.0, "finished": 0.0, "analysis": 0},
    )
    yield
    server._close_channel()


# -- 1. the finalizer must run while the channel is still open ----------------------------------------


def test_drop_finalizes_before_it_closes_the_channel(server):
    holder = [Finalizing()]  # no other reference may exist: the caller's job
    server._drop(holder)
    assert Finalizing.events == ["finalized-open", "closed"]


def test_drop_without_gc_collect_would_close_first(server, monkeypatch):
    """The test above is only meaningful if the object really needs gc.collect(): with the
    collection disabled the channel is closed before the finalizer runs."""
    import gc

    real_collect = gc.collect
    holder = [Finalizing()]
    monkeypatch.setattr(server.gc, "collect", lambda *a: 0)  # server.gc is the gc module itself
    server._drop(holder)
    monkeypatch.undo()
    real_collect()
    assert Finalizing.events == ["closed", "finalized-after-close"]


def test_failed_run_finalizes_before_closing(server, monkeypatch):
    """_close_channel() used to run inside `except _RunFailed`, where the traceback still held the
    connection object: the channel was closed first and the process died (exit 139)."""
    sess = Finalizing(fail_with=RuntimeError("transport failure"))
    monkeypatch.setattr(server, "_mechanical", sess)
    del sess
    out = server._run("print(1)")
    assert "connection dropped" in out and "may or may not have run" in out
    assert Finalizing.events == ["finalized-open", "closed"]
    assert server._mechanical is None


def test_failed_run_through_a_tool_also_finalizes_first(server, monkeypatch):
    sess = Finalizing(fail_with=TimeoutError("deadline"))
    monkeypatch.setattr(server, "_mechanical", sess)
    del sess
    out = J(server.get_mesh_statistics())
    assert out["ok"] is False
    assert Finalizing.events == ["finalized-open", "closed"]


def test_rejected_connection_is_finalized_before_closing(server, monkeypatch):
    """_connect used to drop the half-open connection from inside its except block."""
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply=None))  # never answers with JSON
    with pytest.raises(ConnectionError, match="did not answer"):
        server._connect(4242)
    assert Finalizing.events == ["finalized-open", "closed"]
    assert server._mechanical is None and not server._STATE_FILE.exists()


def test_a_mechanical_that_does_not_answer_is_not_kept(server, monkeypatch):
    """_connect must reject a session that runs scripts but returns nothing usable."""
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply="not json at all"))
    out = J(server.connect_to_mechanical(4243))
    assert out["ok"] is False and server._mechanical is None


def test_a_script_error_during_the_check_is_rejected(server, monkeypatch):
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply="Script error: no model"))
    out = J(server.connect_to_mechanical(4243))
    assert out["ok"] is False and "no model" in out["error"]


def test_replacing_a_connection_finalizes_the_old_one_first(server, monkeypatch):
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply=info_reply()))
    assert J(server.connect_to_mechanical(4244))["ok"] is True
    assert J(server.connect_to_mechanical(4244))["ok"] is True
    assert Finalizing.events == ["finalized-open", "closed"]  # exactly the first one, in order


def test_a_late_connection_after_a_timeout_is_dropped_properly(server, monkeypatch):
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 0.2)
    gate = threading.Event()

    def make(port):
        gate.wait(5)
        return Finalizing(reply=info_reply())

    install_fake_core(monkeypatch, make)
    t0 = time.time()
    out = J(server.connect_to_mechanical(4245))
    assert out["ok"] is False and "did not answer" in out["error"] and time.time() - t0 < 3
    gate.set()
    deadline = time.time() + 5
    while "closed" not in Finalizing.events and time.time() < deadline:
        time.sleep(0.05)
    assert Finalizing.events == ["finalized-open", "closed"]


# -- 6. a socket that is not Mechanical -----------------------------------------------------------------


def test_a_stalled_handshake_does_not_freeze_the_server(server, monkeypatch):
    """A peer that accepts the connection, speaks gRPC enough to pass the probe, then stalls in
    pymechanical: the handshake is bounded and holds no lock."""
    release = threading.Event()
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "_silent", lambda p, wait=2.0: False)
    install_fake_core(monkeypatch, lambda p: (release.wait(10), Finalizing(reply=info_reply()))[1])
    result = {}

    def attempt():
        t = time.time()
        result["r"] = J(server.connect_to_mechanical(4999))
        result["t"] = time.time() - t

    th = threading.Thread(target=attempt)
    th.start()
    time.sleep(0.15)
    # while the handshake is pending the Mechanical lock is free and tools answer immediately
    assert server._MECH_LOCK.acquire(blocking=False)
    server._MECH_LOCK.release()
    t0 = time.time()
    other = J(server.get_mesh_statistics())
    assert time.time() - t0 < 0.5 and other["ok"] is False  # "not connected", at once
    th.join(5)
    release.set()
    assert result["r"]["ok"] is False and "did not answer within 0.5 s" in result["r"]["error"]
    assert result["t"] < 3


def _silent_socket():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    return srv, srv.getsockname()[1]


def test_silent_sockets_never_reach_pymechanical_and_do_not_lock_out_a_good_connect(
    server, monkeypatch
):
    """Two silent local ports used to pin the handshake threads for ~120 s and refuse every
    connect after that. The protocol probe rejects them in about two seconds, without a thread,
    and a good session still connects right after."""
    called = []
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 5)
    install_fake_core(
        monkeypatch, lambda port: called.append(port) or Finalizing(reply=info_reply())
    )
    s1, p1 = _silent_socket()
    s2, p2 = _silent_socket()
    try:
        before = threading.active_count()
        t0 = time.time()
        for p in (p1, p2):
            out = J(server.connect_to_mechanical(p))
            assert out["ok"] is False and "does not speak gRPC" in out["error"]
        assert time.time() - t0 < 8
        assert called == [] and threading.active_count() <= before + 1
        # a real listener that answers like a gRPC server (any bytes) is let through
        good = socket.socket()
        good.bind(("127.0.0.1", 0))
        good.listen(5)
        gp = good.getsockname()[1]

        def answer():
            for _ in range(4):  # the server probes, then connects
                try:
                    c, _addr = good.accept()
                except OSError:
                    return
                threading.Thread(target=reply, args=(c,), daemon=True).start()

        def reply(c):
            try:
                c.settimeout(1.0)
                c.recv(64)
                c.sendall(bytes([0, 0, 0, 4, 0, 0, 0, 0, 0]))
                time.sleep(0.3)
            except OSError:
                pass
            finally:
                c.close()

        threading.Thread(target=answer, daemon=True).start()
        out = J(server.connect_to_mechanical(gp))
        assert out["ok"] is True and called == [gp]
        good.close()
    finally:
        s1.close()
        s2.close()


def test_the_probe_distinguishes_silent_from_closed_and_answering(server):
    assert server._silent(1, 0.2) is False  # nothing listening: not "silent"
    s1, p1 = _silent_socket()
    try:
        assert server._silent(p1, 0.3) is True
    finally:
        s1.close()


def test_the_handshake_is_not_made_while_holding_the_mechanical_lock(server, monkeypatch):
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    seen = {}

    def make(port):
        got = server._MECH_LOCK.acquire(blocking=False)
        seen["free"] = got
        if got:
            server._MECH_LOCK.release()
        return Finalizing(reply=info_reply())

    install_fake_core(monkeypatch, make)
    assert J(server.connect_to_mechanical(4246))["ok"] is True
    assert seen["free"] is True


# -- 4. busy but connected -----------------------------------------------------------------------------------


def _hold_lock(server, seconds):
    held, release = threading.Event(), threading.Event()

    def run():
        with server._MECH_LOCK:
            held.set()
            release.wait(seconds)

    t = threading.Thread(target=run)
    t.start()
    held.wait(2)
    return t, release


def test_status_tools_report_busy_instead_of_waiting_or_denying(server, monkeypatch):
    sess = Finalizing()
    monkeypatch.setattr(server, "_mechanical", sess)
    monkeypatch.setattr(server, "_port", 4321)
    monkeypatch.setattr(server, "PROBE_WAIT_S", 0.1)
    monkeypatch.setattr(server, "LOCK_WAIT_S", 30.0)
    t, release = _hold_lock(server, 5)
    try:
        t0 = time.time()
        chk = J(server.check_mechanical_connection())
        st = J(server.get_solve_status(0))
        dis = J(server.disconnect_from_mechanical())
        assert time.time() - t0 < 3  # nowhere near LOCK_WAIT_S
    finally:
        release.set()
        t.join()
    assert chk["connected"] is True and chk["busy"] is True
    assert st["ok"] is True and st["busy"] is True and st["status"] == "Busy"
    assert dis["ok"] is False and dis["busy"] is True and dis["connected"] is True
    assert server._mechanical is sess  # the busy disconnect did not drop it


def test_busy_error_says_connected(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", Finalizing())
    out = J(server._busy())
    assert out["busy"] is True and out["connected"] is True


# -- 11. reconnect policy ---------------------------------------------------------------------------------------


def _remember(server, port=5001, project="C:/proj/"):
    server._remember_port(port, project)


def test_state_file_remembers_the_project(server):
    _remember(server, 5001, "C:/proj/")
    ident = paths.project_identity("C:/proj/")
    assert server._last_state() == {"port": 5001, "project": ident["id"], "name": "proj"}
    raw = server._STATE_FILE.read_text(encoding="utf-8")
    assert "C:/proj" not in raw and "C:\\proj" not in raw  # never the path itself
    assert server._last_port() == 5001


def test_auto_reconnect_reports_where_it_reconnected(server, monkeypatch):
    _remember(server)
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply=info_reply("C:/proj/")))
    assert server._check_connection() is None
    assert server._mechanical is not None
    assert server._RECONNECTED == {"port": 5001, "project": "proj"}
    out = J(server._with_reconnect_note(json.dumps({"ok": True})))
    assert out["reconnected_to"] == {"port": 5001, "project": "proj"}
    assert not server._RECONNECTED  # reported once


def test_auto_reconnect_refuses_a_different_project(server, monkeypatch):
    """A new conversation must not silently drive another session that now owns the port."""
    _remember(server, 5001, "C:/old-project/")
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply=info_reply("C:/new/")))
    err = J(server._check_connection())
    assert err["ok"] is False and "different project" in err["error"]
    assert server._mechanical is None
    assert not server._STATE_FILE.exists()
    assert Finalizing.events == ["finalized-open", "closed"]


def test_one_transient_failure_does_not_forget_the_port(server, monkeypatch):
    _remember(server)
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply="garbage"))
    for _ in range(server.MAX_RECONNECT_FAILS - 1):
        assert J(server._check_connection())["ok"] is False
        assert server._STATE_FILE.exists()
    assert J(server._check_connection())["ok"] is False
    assert not server._STATE_FILE.exists()  # forgotten only after repeated failures


def test_a_refused_port_is_forgotten_at_once(server, monkeypatch):
    _remember(server)
    monkeypatch.setattr(server, "_port_open", lambda p: False)
    err = J(server._check_connection())
    assert err["ok"] is False and not server._STATE_FILE.exists()


def test_nothing_remembered_means_not_connected(server):
    assert "Not connected" in J(server._check_connection())["error"]


def test_lost_connection_between_check_and_run_is_retried(server, monkeypatch):
    calls = {"n": 0, "checks": 0}
    monkeypatch.setattr(
        server, "_check_connection", lambda: calls.__setitem__("checks", calls["checks"] + 1)
    )

    def run(script, safe_text=True):
        calls["n"] += 1
        return "Error: not connected to Mechanical" if calls["n"] == 1 else '{"ok": true}'

    monkeypatch.setattr(server, "_run", run)
    err, raw = server._checked_run("x")
    assert err is None and raw == '{"ok": true}' and calls["checks"] == 2


# -- shutdown ----------------------------------------------------------------------------------------------------


def test_shutdown_waits_for_the_port_to_close(server, monkeypatch):
    sess = Finalizing()
    sess.exit = lambda force=False: None
    monkeypatch.setattr(server, "_mechanical", sess)
    monkeypatch.setattr(server, "_port", 7777)
    answers = iter([True, True, False])
    monkeypatch.setattr(server, "_port_open", lambda p: next(answers, False))
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    out = J(server.disconnect_from_mechanical(True, "SHUT DOWN MECHANICAL"))
    assert out["ok"] is True and out["process_exited"] is True


def test_shutdown_reports_a_session_that_is_still_there(server, monkeypatch):
    sess = Finalizing()
    sess.exit = lambda force=False: None
    monkeypatch.setattr(server, "_mechanical", sess)
    monkeypatch.setattr(server, "_port", 7777)
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "SHUTDOWN_WAIT_S", 0.3)
    out = J(server.disconnect_from_mechanical(True, "SHUT DOWN MECHANICAL"))
    assert out["ok"] is True and out["process_exited"] is False and "still open" in out["message"]


# -- background solve ---------------------------------------------------------------------------------------------


def test_a_second_background_start_is_refused_without_waiting_for_the_lock(server, monkeypatch):
    gate = threading.Event()

    class Sess(Finalizing):
        def run_python_script(self, script):
            gate.wait(5)
            return ""

    monkeypatch.setattr(server, "_mechanical", Sess())
    monkeypatch.setattr(server, "LOCK_WAIT_S", 30.0)
    assert J(server.solve_analysis(0, wait=False))["started"] is True
    time.sleep(0.2)  # the solve thread now holds the Mechanical lock
    t0 = time.time()
    out = J(server.solve_analysis(0, wait=False))
    assert time.time() - t0 < 1.0 and out["ok"] is False and "still running" in out["error"]
    gate.set()
    server._BG["thread"].join(5)


def test_the_background_result_survives_a_failed_status_poll(server, monkeypatch):
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": json.dumps({"ok": False, "error": "solver crashed"}),
            "started": time.time(),
            "finished": time.time(),
            "analysis": 0,
        },
    )
    monkeypatch.setattr(server, "_check_connection", lambda: server._not_connected())
    assert J(server.get_solve_status(0))["ok"] is False
    assert server._BG["result"] is not None  # still there for the next poll


class _Answers(Finalizing):
    def run_python_script(self, script):
        import re
        from pathlib import Path

        paths = re.findall(r'u"([^"]+)"', script)
        Path(paths[1]).write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "Done",
                    "solved": True,
                    "solve_required": False,
                    "messages": [],
                }
            ),
            encoding="utf-8",
        )
        return ""


def test_the_background_result_goes_only_to_the_same_analysis_and_only_once(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", _Answers())
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": json.dumps({"ok": False, "error": "solver crashed", "marker": 1}),
            "started": time.time(),
            "finished": time.time(),
            "analysis": 2,
        },
    )
    other = J(server.get_solve_status(0))
    assert "last_solve" not in other and server._BG["result"] is not None  # different analysis
    mine = J(server.get_solve_status(2))
    assert mine["last_solve"]["marker"] == 1
    assert "last_solve" not in J(server.get_solve_status(2))  # consumed


def test_an_old_unread_result_is_dropped(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", _Answers())
    old = time.time() - server.BG_RESULT_TTL_S - 10
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": json.dumps({"ok": True, "marker": 9}),
            "started": old - 5,
            "finished": old,
            "analysis": 0,
        },
    )
    assert "last_solve" not in J(server.get_solve_status(0))


def test_a_new_solve_discards_the_previous_unread_result(server, monkeypatch):
    sess = _Answers()
    monkeypatch.setattr(server, "_mechanical", sess)
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": json.dumps({"ok": True, "marker": 5}),
            "started": 1.0,
            "finished": 2.0,
            "analysis": 0,
        },
    )
    server.solve_analysis(0, wait=False)
    server._BG["thread"].join(5)
    st = J(server.get_solve_status(0))
    assert st["last_solve"].get("marker") != 5
