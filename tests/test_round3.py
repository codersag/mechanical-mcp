"""Third review round."""

import asyncio
import json
import threading
import time

import pytest
from fake_mech import Obj, Q
from helpers import Finalizing, install_fake_core

import mechanical_scripts as ms


def J(s):
    return json.loads(s)


@pytest.fixture
def geo(mech):
    fake, s = mech
    b1 = fake.add_body("Plate", part="PartA")
    b2 = fake.add_body("Block", part="PartB")
    fake.add_face(b1, 11, (0.1, 0, 0))
    fake.add_face(b1, 12, (0.3, 0, 0))
    fake.add_face(b2, 21, (0.5, 0, 0))
    fake.build_geo()
    fake.add_ns("Base", [11, 12])
    return fake, s


# -- 1. contact reuse ----------------------------------------------------------------------------------


def test_contact_with_other_friction_is_not_silently_reused(geo):
    fake, s = geo
    first = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2))
    assert first["created"] is True
    same = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2))
    assert same["already_exists"] is True and same["updated"] == {}
    other = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.5))
    assert (
        other["ok"] is False
        and "friction_coefficient" in other["error"]
        and "update_existing" in other["error"]
    )
    assert (
        fake.model.ContactRegions[0].FrictionCoefficient == 0.2
        and len(fake.model.ContactRegions) == 1
    )


def test_update_existing_changes_type_and_friction_and_reports_it(geo):
    fake, s = geo
    s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2)
    out = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.5, True, True))
    assert out["ok"] is True and out["updated"] == {"friction_coefficient": [0.2, 0.5]}
    assert fake.model.ContactRegions[0].FrictionCoefficient == 0.5
    out = J(s.add_contact("rough", "11", "", "", "21", "", "", "face", 0.0, True, True))
    assert (
        out["updated"]["contact_type"] == ["Frictional", "Rough"]
        and len(fake.model.ContactRegions) == 1
    )


def test_a_failed_update_restores_type_and_friction(geo):
    fake, s = geo
    s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2)
    reg = fake.model.ContactRegions[0]
    reg._fail["FrictionCoefficient"] = RuntimeError("rejected")
    out = J(s.add_contact("rough", "11", "", "", "21", "", "", "face", 0.0, True, True))
    reg._fail.clear()
    assert out["ok"] is True  # rough does not touch friction: the update works
    reg.ContactType = "Frictional"
    reg._fail["FrictionCoefficient"] = RuntimeError("rejected")
    out = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.7, True, True))
    assert (
        out["ok"] is False
        and str(reg.ContactType) == "Frictional"
        and reg.FrictionCoefficient == 0.2
    )


def test_reusing_a_suppressed_contact_or_result_says_so(geo):
    fake, s = geo
    s.add_contact("bonded", "11", "", "", "21", "", "", "face", 0.0)
    fake.model.ContactRegions[0].Suppressed = True
    out = J(s.add_contact("bonded", "11", "", "", "21", "", "", "face", 0.0))
    assert out["already_exists"] is True and out["suppressed"] is True
    s.add_equivalent_stress()
    fake.static.Solution.Children[0].Suppressed = True
    out = J(s.add_equivalent_stress())
    assert out["already_exists"] is True and out["suppressed"] is True
    assert J(s.add_total_deformation())["suppressed"] is False


# -- 2. create_named_selection with unmatched names -----------------------------------------------------


def test_unmatched_body_names_are_an_error(geo):
    fake, s = geo
    before = len(fake.model.NamedSelections)
    out = J(s.create_named_selection("n", body_names="Plate,Nope,Typo"))
    assert out["ok"] is False and "Nope" in out["error"] and "Typo" in out["error"]
    assert len(fake.model.NamedSelections) == before
    out = J(s.create_named_selection("n", body_names="Plate,Nope", allow_missing=True))
    assert out["ok"] is True and out["entities"] == 1 and out["missing"] == ["Nope"]
    assert J(s.create_named_selection("m", body_names="Plate,Block"))["missing"] == []


# -- 3/4. linearize: gaps judged by local scale, endpoints on boundaries ----------------------------------------

pytest.importorskip("numpy")
pytest.importorskip("scipy")
import mechanical_postproc as pp  # noqa: E402


def grid(nx, ny, dx=1.0, dy=1.0, f=lambda x, y: 3.0 * y, skip=None):
    pts, vals = [], []
    for i in range(nx):
        for j in range(ny):
            x, y = i * dx, j * dy
            if skip and skip(x, y):
                continue
            pts.append([x, y, 0.0])
            vals.append(f(x, y))
    return pts, vals


def write(path, pts, vals, **extra):
    doc = {
        "result": "Normal Stress",
        "signed": True,
        "xyz_mm": pts,
        "values": vals,
        "value_unit": "Pa",
    }
    doc.update(extra)
    path.write_text(json.dumps(doc))
    return str(path)


def test_coincident_interface_nodes_do_not_fake_a_gap(tmp_path):
    p1, v1 = grid(11, 6, f=lambda x, y: y)
    p2, v2 = [[x, y + 5.0, 0.0] for x, y, _ in p1], [y + 5.0 for _, y, _ in p1]
    f = write(tmp_path / "n.json", p1 + p2, v1 + v2)  # the y=5 row exists twice
    r = pp.linearize_file(f, (5.0, 0.0), (5.0, 10.0), "xy")
    assert r["merged_nodes"] == 11 and r["interpolated_across_gap"] is False
    assert r["membrane"] == pytest.approx(5.0, abs=1e-6)


@pytest.mark.parametrize("aspect", [4, 10])
def test_long_elements_along_the_line_are_not_gaps(tmp_path, aspect):
    pts, vals = grid(6, 41, dx=1.0, dy=float(aspect), f=lambda x, y: 0.5 * y)
    f = write(tmp_path / "n.json", pts, vals)
    top = 40 * aspect
    r = pp.linearize_file(f, (2.5, 0.0), (2.5, float(top)), "xy")
    assert r["interpolated_across_gap"] is False
    assert r["membrane"] == pytest.approx(0.25 * top, rel=1e-6)


def test_anisotropic_mesh_across_the_thin_direction(tmp_path):
    pts, vals = grid(41, 6, dx=10.0, dy=1.0, f=lambda x, y: 2.0 * y)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (205.0, 0.0), (205.0, 5.0), "xy")
    assert r["interpolated_across_gap"] is False and r["membrane"] == pytest.approx(5.0, abs=1e-6)


def test_a_graded_polar_mesh_is_not_flagged(tmp_path):
    import math

    pts, vals = [], []
    radii = [2.0 * 1.2**i for i in range(14)]
    for r in radii:
        for k in range(48):
            th = 2 * math.pi * k / 48
            pts.append([r * math.cos(th), r * math.sin(th), 0.0])
            vals.append(r)
    f = write(tmp_path / "n.json", pts, vals)
    th = math.pi / 48  # half way between two rays
    a = (radii[1] * math.cos(th), radii[1] * math.sin(th))
    b = (radii[11] * math.cos(th), radii[11] * math.sin(th))
    res = pp.linearize_file(f, a, b, "xy")
    assert res["interpolated_across_gap"] is False
    assert res["membrane"] == pytest.approx(0.5 * (radii[1] + radii[11]), rel=0.05)


def test_a_coarse_mesh_is_fine(tmp_path):
    pts, vals = grid(3, 3, dx=10.0, dy=10.0, f=lambda x, y: y)
    f = write(tmp_path / "n.json", pts, vals)
    assert pp.linearize_file(f, (10.0, 0.0), (10.0, 20.0), "xy")["membrane"] == pytest.approx(10.0)


def test_a_real_hole_and_a_real_notch_are_still_refused(server, tmp_path):
    hole, hv = grid(21, 21, f=lambda x, y: y, skip=lambda x, y: (x - 10) ** 2 + (y - 10) ** 2 < 16)
    f = write(tmp_path / "hole.json", hole, hv)
    with pytest.raises(pp.GapError) as e:
        pp.linearize_file(f, (10.0, 0.0), (10.0, 20.0), "xy")
    assert e.value.diagnostics["largest_span_length_mm"] > 3.0
    notch, nv = grid(21, 21, f=lambda x, y: y, skip=lambda x, y: 8 <= x <= 12 and y >= 6)
    f2 = write(tmp_path / "notch.json", notch, nv)
    with pytest.raises(pp.GapError):
        pp.linearize_file(f2, (10.0, 0.0), (10.0, 20.0), "xy")
    out = J(server.linearize_path(nodal_file=f, start_mm="10,0", end_mm="10,20"))
    assert out["ok"] is False and out["gap_diagnostics"]["largest_span_mm"]
    out = J(server.linearize_path(nodal_file=f, start_mm="10,0", end_mm="10,20", allow_gaps=True))
    assert out["ok"] is True and out["interpolated_across_gap"] is True


def test_line_ends_a_hair_outside_a_flat_face_are_snapped(tmp_path):
    pts, vals = grid(11, 11, f=lambda x, y: y)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (5.0, -1e-9), (5.0, 10.0 + 1e-9), "xy")
    assert r["membrane"] == pytest.approx(5.0, abs=1e-6)
    assert {a["sample"] for a in r["endpoint_adjustments"]} == {0, 80}


def test_a_line_that_really_leaves_the_mesh_is_refused(tmp_path):
    pts, vals = grid(11, 11, f=lambda x, y: y)
    f = write(tmp_path / "n.json", pts, vals)
    with pytest.raises(ValueError, match="leaves the meshed region"):
        pp.linearize_file(f, (5.0, -3.0), (5.0, 10.0), "xy")
    with pytest.raises(ValueError, match="snapping tolerance|leaves the meshed region"):
        pp.linearize_file(f, (5.0, -0.5), (5.0, 10.0), "xy", snap_fraction=0.05)


def test_a_start_on_the_chord_of_a_curved_hole_edge_works(tmp_path):
    import math

    pts, vals = [], []
    for i in range(11):
        r = 5.0 + i
        for k in range(48):
            th = 2 * math.pi * k / 48
            pts.append([r * math.cos(th), r * math.sin(th), 0.0])
            vals.append(r)
    f = write(tmp_path / "n.json", pts, vals)
    th = math.pi / 48
    rin = 5.0 * math.cos(
        th
    )  # midpoint of the chord between two ring nodes: inside the hole polygon
    res = pp.linearize_file(
        f,
        (rin * math.cos(th), rin * math.sin(th)),
        (14.0 * math.cos(th), 14.0 * math.sin(th)),
        "xy",
    )
    assert res["interpolated_across_gap"] is False and res["membrane"] == pytest.approx(
        9.5, rel=0.05
    )


# -- 9. component / unit honesty -------------------------------------------------------------------------------


def test_expect_component_without_a_recorded_orientation_is_flagged(tmp_path):
    pts, vals = grid(11, 11, f=lambda x, y: y)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (5.0, 0.0), (5.0, 10.0), "xy", expect_component="y")
    assert r["component_unverified"] is True and any("does not record" in w for w in r["warnings"])


def test_a_line_along_another_axis_than_the_component_is_refused(tmp_path):
    pts, vals = grid(11, 11, f=lambda x, y: x)
    f = write(tmp_path / "n.json", pts, vals, orientation="ZAxis")
    with pytest.raises(ValueError, match="runs along X but the export holds the ZAxis"):
        pp.linearize_file(f, (0.0, 5.0), (10.0, 5.0), "xy")
    r = pp.linearize_file(f, (0.0, 5.0), (10.0, 5.0), "xy", accept_direction_mismatch=True)
    assert r["direction_mismatch"] is True
    g = write(tmp_path / "m.json", pts, vals, orientation="XAxis")
    assert "direction_mismatch" not in pp.linearize_file(g, (0.0, 5.0), (10.0, 5.0), "xy")


def test_value_unit_is_only_reported_when_verified(tmp_path):
    pts, vals = grid(11, 11, f=lambda x, y: y)
    ok = pp.linearize_file(
        write(tmp_path / "a.json", pts, vals, value_unit_verified=True),
        (5.0, 0.0),
        (5.0, 10.0),
        "xy",
    )
    assert ok["value_unit"] == "Pa" and ok["value_unit_verified"] is True
    no = pp.linearize_file(
        write(tmp_path / "b.json", pts, vals, value_unit_verified=False),
        (5.0, 0.0),
        (5.0, 10.0),
        "xy",
    )
    assert no["value_unit"] == "unverified" and no["value_unit_claimed"] == "Pa" and no["warnings"]
    old = pp.linearize_file(write(tmp_path / "c.json", pts, vals), (5.0, 0.0), (5.0, 10.0), "xy")
    assert old["value_unit"] == "unknown"


# -- 5. pools and the waiter cap ----------------------------------------------------------------------------------


def test_status_tools_are_not_starved_by_queued_calls(server, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(server, "_run", lambda sc, safe_text=True: (gate.wait(10), "{}")[1])
    monkeypatch.setattr(server, "_check_connection", lambda: None)

    async def main():
        slow = [asyncio.create_task(server.mcp.call_tool("list_materials", {})) for _ in range(12)]
        await asyncio.sleep(0.3)  # every worker of the main pool is now busy
        t0 = time.time()
        res = await asyncio.wait_for(server.mcp.call_tool("check_mechanical_connection", {}), 4)
        took = time.time() - t0
        gate.set()
        await asyncio.gather(*slow, return_exceptions=True)
        return took, res

    took, res = asyncio.run(main())
    assert took < 2 and "connected" in str(res)


def test_calls_beyond_the_waiter_cap_are_refused_at_once(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", Finalizing())
    monkeypatch.setattr(server, "LOCK_WAIT_S", 1.5)
    held, release = threading.Event(), threading.Event()

    def hold():
        with server._MECH_LOCK:
            held.set()
            release.wait(5)

    threading.Thread(target=hold).start()
    held.wait(2)
    results = []

    def call():
        t = time.time()
        results.append((server._run("x"), time.time() - t))

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.4)
    quick = [r for r in results if r[1] < 0.3]
    release.set()
    for t in threads:
        t.join()
    assert len(quick) == 8 - server.MAX_WAITERS and all("busy" in r[0] for r in quick)


def test_a_busy_refusal_carries_the_busy_flag(server):
    out = J(
        server._result(
            "Error: Mechanical is busy with another call (for example a solve). Try again."
        )
    )
    assert out["busy"] is True and out["ok"] is False


# -- 6. export never replaces a file it did not create -------------------------------------------------------------------


def _export_fake(fake, dest):
    fake.static.Solution.Status = "Done"
    res = fake.static.Solution.AddNormalStress()
    res.Name = "Normal Stress"
    res.EvaluateAllResults = lambda: None
    res.Maximum = Q(3.0, "Pa")

    def plotdata(self):
        if getattr(self, "_appear", None) and not dest.exists():
            dest.write_text("APPEARED")
        return {"Node": [1, 2, 3], "Values": [1.0, 2.0, 3.0]}

    res.__class__ = type("R", (Obj,), {"PlotData": property(plotdata)})
    object.__setattr__(res, "_appear", True)
    nd = {i: Obj("n", X=0.001 * i, Y=0.0, Z=0.0) for i in (1, 2, 3)}
    fake.ext.DataModel.MeshDataByName = lambda n: Obj("md", Unit="m", NodeById=lambda i: nd[i])


def test_a_file_that_appears_during_the_export_is_not_replaced(mech, tmp_path):
    fake, s = mech
    dest = tmp_path / "n.json"
    _export_fake(fake, dest)
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, False)))
    assert out["ok"] is False and "appeared" in out["error"]
    assert dest.read_text() == "APPEARED" and [p.name for p in tmp_path.iterdir()] == ["n.json"]


def test_overwrite_true_may_replace_a_file_that_appeared(mech, tmp_path):
    fake, s = mech
    dest = tmp_path / "n.json"
    _export_fake(fake, dest)
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, True)))
    assert out["ok"] is True and json.loads(dest.read_text())["values"] == [1.0, 2.0, 3.0]


def test_export_node_cap_and_time_budget(mech, tmp_path):
    fake, s = mech
    dest = tmp_path / "n.json"
    _export_fake(fake, dest)
    fake.static.Solution.Children[0]._appear = False
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, False, 2)))
    assert out["ok"] is False and "limit is 2" in out["error"] and not dest.exists()
    nd = {i: Obj("n", X=0.001 * i, Y=0.0, Z=0.0) for i in (1, 2, 3)}

    def slow_node(i):
        time.sleep(0.03)
        return nd[i]

    fake.ext.DataModel.MeshDataByName = lambda n: Obj("md", Unit="m", NodeById=slow_node)
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, False, 100, 0.01)))
    assert out["ok"] is False and "time budget" in out["error"] and not dest.exists()
    assert list(tmp_path.iterdir()) == []


# -- 7. set_contact_type friction -------------------------------------------------------------------------------------------


def _regions(fake, n=2, **kw):
    for _ in range(n):
        r = fake.model.Connections.AddContactRegion()
        for k, v in kw.items():
            setattr(r, k, v)
    return fake.model.ContactRegions


def test_friction_is_restored_with_the_type_on_failure(mech):
    fake, s = mech
    regs = _regions(fake, 2, ContactType="Frictional", FrictionCoefficient=0.2)
    regs[1]._fail["FrictionCoefficient"] = RuntimeError("rejected")
    out = J(s.set_contact_type("frictional", 0.5))
    assert out["ok"] is False and out["restored"] is True
    assert [r.FrictionCoefficient for r in regs] == [0.2, 0.2] and {
        str(r.ContactType) for r in regs
    } == {"Frictional"}


def test_same_type_other_friction_reports_real_before_and_after(mech):
    fake, s = mech
    _regions(fake, 2, ContactType="Frictional", FrictionCoefficient=0.2)
    out = J(s.set_contact_type("frictional", 0.5))
    assert out["regions_changed"] == 2
    c = out["changed"][0]
    assert c["was"] == "Frictional" and c["now"] == "Frictional"
    assert c["friction_was"] == 0.2 and c["friction_now"] == 0.5
    assert J(s.set_contact_type("frictional", 0.5))["regions_changed"] == 0


# -- 8. point-mass duplicate rule ---------------------------------------------------------------------------------------------


def _scene(fake, name, part="Equip"):
    eq = fake.add_body(name, part=part, mass=0.008)
    for i, c in enumerate([(0, 0, 0), (0, 0, 0.02), (0.01, 0, 0.01), (-0.01, 0, 0.01)]):
        fake.add_face(eq, 10 + i + (len(fake.model.Geometry.Bodies) * 20), c, normal=(0, 0, 1))
    eq.CentroidZ = Q(0.01, "m")
    return eq


def test_a_prefix_that_merely_starts_like_another_is_not_a_duplicate(mech):
    fake, s = mech
    _scene(fake, "SYS\\Backing")
    _scene(fake, "SYS\\B")
    plate = fake.add_body("Plate", part="Struct", mass=50.0)
    fake.add_face(plate, 900, (0.0, 0.0, 0.0), normal=(0, 0, 1))
    fake.add_face(plate, 901, (0.025, 0.0, 0.0), normal=(0, 0, 1))
    fake.build_geo()
    fake.model.Geometry.AddPointMass = lambda: _pm(fake)
    assert J(s.convert_prefix_to_point_mass("SYS\\Backing"))["ok"] is True
    out = J(s.convert_prefix_to_point_mass("SYS\\B"))
    assert out["ok"] is True, out  # was refused: "NearestFace_SYS_B" is a prefix of "..._Backing"
    again = J(s.convert_prefix_to_point_mass("SYS\\Backing"))
    assert (
        again["ok"] is False
        and "Already converted" in again["error"]
        and "exact prefix" in again["error"]
    )


def _pm(fake):
    pm = Obj("Point Mass", InternalObject=Obj("io"), Activate=lambda: None, Location=None)
    pm._owner = fake.model.Geometry.PointMasses
    fake.model.Geometry.PointMasses.append(pm)
    return pm


def test_labels_of_long_prefixes_do_not_collide():
    a = "X" * 60 + "A"
    b = "X" * 60 + "B"
    assert ms.ns_label(a) != ms.ns_label(b) and ms.ns_label("SYS\\B") != ms.ns_label("SYS\\Backing")
    assert len(ms.ns_label(a)) < 40


# -- 10. smaller things ----------------------------------------------------------------------------------------------------------------


def test_whole_model_results_say_so(mech):
    fake, s = mech
    s.add_equivalent_stress()
    row = J(s.list_results(0))["results"][0]
    assert row["scope"] == "whole model" and "scope_entities" not in row


def test_status_while_a_background_solve_runs_names_the_analysis(server, monkeypatch):
    alive = threading.Event()
    t = threading.Thread(target=alive.wait, args=(5,))
    t.start()
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": t,
            "result": None,
            "started": time.time(),
            "finished": 0.0,
            "analysis": 1,
            "history": [],
        },
    )
    monkeypatch.setitem(server._CONN_INFO, "analyses", 3)
    try:
        mine = J(server.get_solve_status(1))
        other = J(server.get_solve_status(0))
        bad = J(server.get_solve_status(7))
    finally:
        alive.set()
        t.join()
    assert mine["status"] == "Solving" and mine["analysis"] == 1
    assert other["status"] == "Busy" and other["solving_analysis"] == 1
    assert bad["ok"] is False and "out of range" in bad["error"]


def test_a_new_background_solve_keeps_the_unread_result_in_the_history(server, monkeypatch):
    from helpers import info_reply  # noqa: F401

    class Answers(Finalizing):
        def run_python_script(self, script):
            import re
            from pathlib import Path

            paths = re.findall(r'u"([^"]+)"', script)
            Path(paths[1]).write_text(
                json.dumps({"ok": True, "status": "Done", "solved": True, "messages": []}),
                encoding="utf-8",
            )

    monkeypatch.setattr(server, "_mechanical", Answers())
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": json.dumps({"ok": True, "marker": 1}),
            "started": time.time() - 5,
            "finished": time.time() - 4,
            "analysis": 0,
            "history": [],
        },
    )
    server.solve_analysis(0, wait=False)
    server._BG["thread"].join(5)
    st = J(server.get_solve_status(0))
    assert st["last_solve"].get("marker") is None  # the newest
    assert [d.get("marker") for d in st["earlier_unread_solves"]] == [1]
    assert "last_solve" not in J(server.get_solve_status(0))


def test_threads_stuck_in_handshakes_are_capped(server, monkeypatch, tmp_path):
    release = threading.Event()
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "p.json")
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 0.15)
    install_fake_core(monkeypatch, lambda port: (release.wait(10), None)[1])
    msgs = [J(server.connect_to_mechanical(4000 + i))["error"] for i in range(4)]
    release.set()
    assert "did not answer" in msgs[0] and "did not answer" in msgs[1]
    assert "too many connection attempts" in msgs[2] and "too many" in msgs[3]
    deadline = time.time() + 5
    while time.time() < deadline and threading.active_count() > 3:
        time.sleep(0.05)


def test_transport_failures_are_summarised_to_one_line(server, monkeypatch):
    raw = (
        "<_MultiThreadedRendezvous of RPC that terminated with:\n\tstatus = StatusCode.UNAVAILABLE\n"
        '\tdetails = "failed to connect to all addresses"\n\tdebug_error_string = "UNKNOWN:..."\n>'
    )
    monkeypatch.setattr(server, "_mechanical", Finalizing(fail_with=RuntimeError(raw)))
    out = J(server.get_mesh_statistics())
    assert out["ok"] is False
    assert out["error"].startswith("gRPC UNAVAILABLE: failed to connect to all addresses")
    assert "\n" not in out["error"] and "debug_error_string" in out["detail"]


# -- 11. robustness -----------------------------------------------------------------------------------------------------------------------


def test_a_result_arriving_at_the_deadline_is_returned_or_dropped_never_lost(server):
    dropped, returned = [], []
    n = 150
    for i in range(n):

        def fn(i=i):
            time.sleep(0.004 + (i % 5) * 0.0005)
            return object()

        try:
            returned.append(
                server._call_with_timeout(fn, 0.0055, on_late=lambda h: dropped.append(h[0]))
            )
        except TimeoutError:
            pass
    time.sleep(0.3)
    assert len(returned) + len(dropped) == n


def test_an_empty_project_identity_is_never_silently_reattached(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "p.json")
    server._remember_port(5001, "")
    made = []
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    install_fake_core(monkeypatch, lambda port: made.append(1))
    out = J(server._check_connection())
    assert out["ok"] is False and "no project identity" in out["error"] and made == []
    assert server._STATE_FILE.exists()


def test_busy_and_timeouts_are_not_refusals(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "p.json")
    server._remember_port(5001, "C:/p/")
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(server, "_RECONNECT_FAILS", 0)
    release = threading.Event()
    install_fake_core(monkeypatch, lambda port: (release.wait(10), None)[1])
    for _ in range(5):
        assert J(server._check_connection())["ok"] is False
        time.sleep(0.12)
    release.set()
    assert server._STATE_FILE.exists() and server._RECONNECT_FAILS == 0


def test_three_bad_answers_forget_the_port_but_two_do_not(server, monkeypatch, tmp_path):
    """Pinned to the literal 3 of the documentation."""
    assert server.MAX_RECONNECT_FAILS == 3
    monkeypatch.setattr(server, "_STATE_FILE", tmp_path / "p.json")
    server._remember_port(5001, "C:/p/")
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "_RECONNECT_FAILS", 0)
    install_fake_core(monkeypatch, lambda port: Finalizing(reply="garbage"))
    for _ in range(2):
        server._check_connection()
        assert server._STATE_FILE.exists()
    server._check_connection()
    assert not server._STATE_FILE.exists()


def test_a_handshake_stuck_in_the_first_script_is_not_closed_under_the_stuck_thread(
    server, monkeypatch, tmp_path
):
    """The connection object is still referenced by the thread running the script: closing its
    channel there would repeat the crash. It must be left to the finalizer."""
    hang = threading.Event()
    monkeypatch.setattr(server, "_port_open", lambda p: True)
    monkeypatch.setattr(server, "CONNECT_TIMEOUT_S", 0.2)
    Finalizing.events = []
    install_fake_core(monkeypatch, lambda port: Finalizing(hang=hang))
    out = J(server.connect_to_mechanical(4999))
    assert out["ok"] is False and "did not run the connection check" in out["error"]
    assert Finalizing.events == []  # nothing closed, nothing finalized yet
    hang.set()
    deadline = time.time() + 5
    import gc

    while not Finalizing.events and time.time() < deadline:
        gc.collect()
        time.sleep(0.05)
    assert Finalizing.events[0] == "finalized-open"


def test_every_tool_reports_a_silent_reattachment_once(mech, tmp_path, monkeypatch):
    fake, s = mech
    s._RECONNECTED.update({"port": 5, "project": "C:/p/"})
    out = J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt"))
    assert out["reconnected_to"]["port"] == 5
    assert "reconnected_to" not in J(s.get_mesh_statistics())
    s._RECONNECTED.update({"port": 6, "project": "C:/q/"})
    monkeypatch.setenv("MECHANICAL_MCP_ENABLE_SCRIPT", "1")
    assert J(s.run_mechanical_script("print(1)"))["reconnected_to"]["port"] == 6


def test_a_second_solve_after_an_unfinished_one_is_refused(mech):
    fake, s = mech
    fake.static.Solution.solve_raises = None
    s._SOLVE_UNKNOWN.clear()
    s._SOLVE_UNKNOWN.update({"analysis": 0, "why": "connection dropped", "at": time.time()})
    out = J(s.solve_analysis(0))
    assert (
        out["ok"] is False
        and "confirm_not_running" in out["error"]
        and fake.static.Solution.solves == 0
    )
    assert J(s.solve_analysis(0, True, True))["ok"] is True and fake.static.Solution.solves == 1
    assert not s._SOLVE_UNKNOWN


def test_a_dropped_solve_call_marks_the_solve_unknown(server, monkeypatch):
    monkeypatch.setattr(server, "_mechanical", Finalizing(fail_with=TimeoutError("deadline")))
    out = J(server.solve_analysis(0))
    assert "connection dropped" in out["error"] and server._SOLVE_UNKNOWN
    monkeypatch.setattr(server, "_mechanical", Finalizing())
    refused = J(server.solve_analysis(0))
    assert "did not come back" in refused["error"]
    assert J(server.solve_analysis(0, False))["ok"] is False  # background start is guarded too


def test_wrapper_paths_with_non_ascii_characters_are_escaped(server):
    w = server._wrapper(
        "C:/Users/Jos\u00e9 \u4e16/Temp/a.py", "C:/Users/Jos\u00e9 \u4e16/Temp/o.txt", True
    )
    assert w.isascii() and "Jos\\u00e9" in w
    compile(w, "wrapper", "exec")


@pytest.mark.parametrize(
    "name",
    [
        "aux.json",
        "COM1.json",
        "nul",
        "con.txt",
        "LPT3.txt",
        "prn.json",
        "COM\u00b9.json",
        "conin$.json",
        "CONOUT$",
        "sub/aux.txt",
        "aux .json",
        "AUX..json",
    ],
)
def test_reserved_device_names_are_refused(server, tmp_path, name):
    p, err = server._allow(str(tmp_path / name), exts={".json", ".txt", ""}, write=True)
    assert p is None and "device name" in json.loads(err)["error"]


@pytest.mark.parametrize("name", ["auxiliary.json", "com10.json", "console.txt", "nulled.json"])
def test_look_alike_names_are_fine(server, tmp_path, name):
    p, err = server._allow(str(tmp_path / name), exts={".json", ".txt"}, write=True)
    assert err is None


def test_the_friction_is_part_of_the_signature_of_a_contact(geo):
    fake, s = geo
    s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2)
    fake.model.ContactRegions[0].FrictionCoefficient = 0.9  # changed by hand since
    out = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.2))
    assert out["ok"] is False and "0.9" in out["error"]


def test_a_failed_update_puts_the_old_type_back(geo):
    """The type is changed first, then the friction; if the friction is rejected the type must
    not stay changed."""
    fake, s = geo
    s.add_contact("rough", "11", "", "", "21", "", "", "face", 0.0)
    reg = fake.model.ContactRegions[0]
    reg._fail["FrictionCoefficient"] = RuntimeError("rejected")
    out = J(s.add_contact("frictional", "11", "", "", "21", "", "", "face", 0.5, True, True))
    assert out["ok"] is False and "rejected" in out["error"]
    assert (
        str(reg.ContactType) == "Rough"
        and out["created"] is False
        and len(fake.model.ContactRegions) == 1
    )


def test_old_entries_in_the_history_are_not_attached(server, monkeypatch):
    from helpers import Finalizing as F

    class Answers(F):
        def run_python_script(self, script):
            import re
            from pathlib import Path

            paths = re.findall(r'u"([^"]+)"', script)
            Path(paths[1]).write_text(
                json.dumps({"ok": True, "status": "Done", "messages": []}), encoding="utf-8"
            )

    old = time.time() - server.BG_RESULT_TTL_S - 100
    monkeypatch.setattr(server, "_mechanical", Answers())
    monkeypatch.setattr(
        server,
        "_BG",
        {
            "thread": None,
            "result": None,
            "started": 0.0,
            "finished": 0.0,
            "analysis": 0,
            "history": [
                {"analysis": 0, "finished": old, "text": json.dumps({"ok": True, "marker": 3})}
            ],
        },
    )
    assert "last_solve" not in J(server.get_solve_status(0))
