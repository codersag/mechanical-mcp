"""Linearization against analytic stress profiles, and linearize_path error handling."""

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import mechanical_postproc as pp  # noqa: E402


def J(s):
    return json.loads(s)


def profile(t, n, f):
    s = [t * i / (n - 1) for i in range(n)]
    return s, [f(x / t) for x in s]


# -- the ASME decomposition ---------------------------------------------------------------------------


def test_pure_membrane():
    s, v = profile(10.0, 201, lambda u: 100.0)
    r = pp.linearize(s, v)
    assert r["membrane"] == pytest.approx(100.0)
    assert r["bending"] == pytest.approx(0.0, abs=1e-9)
    assert r["peak"] == pytest.approx(0.0, abs=1e-9)
    assert r["section_length"] == pytest.approx(10.0)


def test_pure_bending():
    s, v = profile(10.0, 201, lambda u: 40.0 * (1.0 - 2.0 * u))
    r = pp.linearize(s, v)
    assert r["membrane"] == pytest.approx(0.0, abs=1e-9)
    assert r["bending"] == pytest.approx(40.0)
    assert r["mem_plus_bend_start"] == pytest.approx(40.0) and r[
        "mem_plus_bend_end"
    ] == pytest.approx(-40.0)
    assert r["peak"] == pytest.approx(0.0, abs=1e-9)


def test_linear_plus_peak():
    c = 60.0
    s, v = profile(
        8.0, 2001, lambda u: 100.0 + 40.0 * (1 - 2 * u) + c * ((u - 0.5) ** 2 - 1.0 / 12.0)
    )
    r = pp.linearize(s, v)
    assert r["membrane"] == pytest.approx(100.0, abs=1e-3)
    assert r["bending"] == pytest.approx(40.0, abs=1e-3)
    # peak = total - (membrane + bending): c * (1/4 - 1/12) at either surface, -c/12 at the middle
    assert r["peak_at_start"] == pytest.approx(10.0, abs=1e-2) and r[
        "peak_at_end"
    ] == pytest.approx(10.0, abs=1e-2)
    assert r["peak"] == pytest.approx(10.0, abs=1e-2)
    assert r["total_at_start"] == pytest.approx(140.0 + 10.0 + 0.0, abs=1e-2)


def test_peak_is_not_the_largest_total_value():
    """The old code returned max(|v|), i.e. 150 here; the standard asks for the remainder."""
    s, v = profile(10.0, 401, lambda u: 100.0 + 50.0 * (1 - 2 * u))
    r = pp.linearize(s, v)
    assert max(abs(x) for x in v) == pytest.approx(150.0)
    assert r["peak"] == pytest.approx(0.0, abs=1e-8)


def test_a_negative_peak_keeps_its_sign():
    s, v = profile(1.0, 801, lambda u: 5.0 - 30.0 * ((u - 0.5) ** 2 - 1.0 / 12.0))
    r = pp.linearize(s, v)
    assert r["peak"] == pytest.approx(-5.0, abs=1e-2) and r["membrane"] == pytest.approx(
        5.0, abs=1e-3
    )


def test_offset_start_coordinate_is_irrelevant():
    s, v = profile(10.0, 101, lambda u: 100.0 + 40.0 * (1 - 2 * u))
    r = pp.linearize([x + 250.0 for x in s], v)
    assert r["membrane"] == pytest.approx(100.0) and r["bending"] == pytest.approx(40.0)


@pytest.mark.parametrize(
    "s,v",
    [
        ([0.0], [1.0]),
        ([], []),
        ([0.0, 0.0], [1.0, 2.0]),
        ([0.0, 1.0, 0.5], [1, 2, 3]),
        ([0.0, 1.0], [1.0, float("nan")]),
    ],
)
def test_degenerate_profiles_are_value_errors(s, v):
    with pytest.raises(ValueError):
        pp.linearize(s, v)


# -- sampling -------------------------------------------------------------------------------------------


def grid2d(nx=11, ny=21, f=lambda x, y: 100.0 + 5.0 * y, z=0.0):
    pts, vals = [], []
    for x in range(nx):
        for y in range(ny):
            pts.append([float(x), float(y), z])
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


def test_file_roundtrip_2d(tmp_path):
    f = write(tmp_path / "n.json", *grid2d())
    r = pp.linearize_file(f, (5, 0), (5, 20), "xy")
    assert r["membrane"] == pytest.approx(150.0) and r["section_length_mm"] == pytest.approx(20.0)
    assert r["bending"] == pytest.approx(-50.0) and r["peak"] == pytest.approx(0.0, abs=1e-6)
    assert (
        r["value_unit"] == "unknown" and r["value_unit_claimed"] == "Pa" and "thickness_mm" not in r
    )


def test_line_outside_the_mesh(tmp_path):
    f = write(tmp_path / "n.json", *grid2d())
    with pytest.raises(ValueError, match="leaves the meshed region"):
        pp.linearize_file(f, (50, 50), (60, 60), "xy")


def test_two_layers_at_different_depth_are_not_mixed(tmp_path):
    p1, v1 = grid2d(f=lambda x, y: 100.0, z=0.0)
    p2, v2 = grid2d(f=lambda x, y: 500.0, z=10.0)
    f = write(tmp_path / "n.json", p1 + p2, v1 + v2)
    with pytest.raises(ValueError, match="slice_mm"):
        pp.linearize_file(f, (5, 0), (5, 20), "xy")
    r = pp.linearize_file(f, (5, 0), (5, 20), "xy", slice_mm=10.0)
    assert r["membrane"] == pytest.approx(500.0) and r["slice"]["nodes"] == len(v2)
    r = pp.linearize_file(f, (5, 0), (5, 20), "xy", slice_mm=0.0)
    assert r["membrane"] == pytest.approx(100.0)
    with pytest.raises(ValueError):
        pp.linearize_file(f, (5, 0), (5, 20), "xy", slice_mm=99.0)


def test_3d_line_through_a_solid(tmp_path):
    pts, vals = [], []
    for x in range(5):
        for y in range(5):
            for z in range(11):
                pts.append([float(x), float(y), float(z)])
                vals.append(200.0 - 10.0 * z)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (2.0, 2.0, 0.0), (2.0, 2.0, 10.0))
    assert r["membrane"] == pytest.approx(150.0) and r["bending"] == pytest.approx(50.0)
    assert r["section_length_mm"] == pytest.approx(10.0)


def test_other_planes(tmp_path):
    pts, vals = [], []
    for x in range(6):
        for z in range(6):
            pts.append([float(x), 3.0, float(z)])
            vals.append(10.0 * z)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (2, 0), (2, 5), "xz")
    assert r["membrane"] == pytest.approx(25.0)


@pytest.mark.parametrize("n", [1, 0, -3, True, 2.5])
def test_bad_sample_counts(tmp_path, n):
    f = write(tmp_path / "n.json", *grid2d())
    with pytest.raises(ValueError):
        pp.linearize_file(f, (5, 0), (5, 20), "xy", n)


def test_zero_length_line(tmp_path):
    f = write(tmp_path / "n.json", *grid2d())
    with pytest.raises(ValueError, match="no length"):
        pp.linearize_file(f, (5, 5), (5, 5), "xy")


def test_collinear_nodes(tmp_path):
    pts = [[float(i), 0.0, 0.0] for i in range(10)]
    f = write(tmp_path / "n.json", pts, [1.0] * 10)
    with pytest.raises(ValueError, match="triangulated"):
        pp.linearize_file(f, (0, -1), (9, 1), "xy")


def test_too_few_nodes(tmp_path):
    f = write(tmp_path / "n.json", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], [1.0, 2.0])
    with pytest.raises(ValueError):
        pp.linearize_file(f, (0, 0), (1, 0), "xy")


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"xyz_mm": 1, "values": 2},
        {"xyz_mm": [[0, 0]], "values": [1]},
        {"xyz_mm": [[0, 0, 0]], "values": []},
        {},
    ],
)
def test_malformed_export_files(tmp_path, doc):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        pp.linearize_file(str(f), (0, 0), (1, 1))


def test_nan_in_the_export(tmp_path):
    pts, vals = grid2d()
    vals[3] = float("nan")
    f = write(tmp_path / "n.json", pts, vals)
    with pytest.raises(ValueError, match="NaN"):
        pp.linearize_file(f, (5, 0), (5, 20))


# -- unsigned quantities --------------------------------------------------------------------------------


def test_unsigned_quantities_are_refused_unless_forced(tmp_path):
    f = write(tmp_path / "n.json", *grid2d(), signed=False, result="Equivalent Stress")
    with pytest.raises(ValueError, match="unsigned"):
        pp.linearize_file(f, (5, 0), (5, 20))
    assert pp.linearize_file(f, (5, 0), (5, 20), allow_unsigned=True)["signed"] is False


def test_old_files_without_the_flag_are_judged_by_name(tmp_path):
    pts, vals = grid2d()
    doc = tmp_path / "old.json"
    doc.write_text(
        json.dumps({"result": "Equivalent Stress (von Mises)", "xyz_mm": pts, "values": vals})
    )
    with pytest.raises(ValueError):
        pp.linearize_file(str(doc), (5, 0), (5, 20))
    doc.write_text(json.dumps({"result": "Normal Stress", "xyz_mm": pts, "values": vals}))
    assert pp.linearize_file(str(doc), (5, 0), (5, 20))["membrane"] == pytest.approx(150.0)


# -- the tool ---------------------------------------------------------------------------------------------


def test_tool_reports_every_failure_as_a_clean_error(server, tmp_path):
    good = write(tmp_path / "n.json", *grid2d())
    coll = write(tmp_path / "c.json", [[float(i), 0.0, 0.0] for i in range(10)], [1.0] * 10)
    cases = [
        dict(nodal_file=good, start_mm="5,0", end_mm="5,20", samples=1),
        dict(nodal_file=good, start_mm="5,0", end_mm="5,20", samples=0),
        dict(nodal_file=good, start_mm="5,5", end_mm="5,5"),
        dict(nodal_file=coll, start_mm="0,-1", end_mm="9,1"),
        dict(nodal_file=good, start_mm="5,0", end_mm="5"),
        dict(nodal_file=good, start_mm="5,0", end_mm="5,nan"),
        dict(nodal_file=good, start_mm="5,0", end_mm="5,20,1"),
        dict(nodal_file=good, start_mm="5,0", end_mm="5,20", plane="ab"),
        dict(nodal_file=good, start_mm="5,0", end_mm="5,20", samples=10**9),
        dict(nodal_file=str(tmp_path / "missing.json"), start_mm="5,0", end_mm="5,20"),
        dict(nodal_file=str(tmp_path / "n.txt"), start_mm="5,0", end_mm="5,20"),
        dict(nodal_file="\\\\host\\share\\n.json", start_mm="5,0", end_mm="5,20"),
    ]
    for kw in cases:
        out = J(server.linearize_path(**kw))
        assert out["ok"] is False and out["error"], kw
        assert (
            "Traceback" not in out["error"]
            and "ZeroDivision" not in out["error"]
            and "IndexError" not in out["error"]
        )
    out = J(server.linearize_path(nodal_file=good, start_mm="5,0", end_mm="5,20"))
    assert out["ok"] is True and out["membrane"] == pytest.approx(150.0)


def test_tool_refuses_files_outside_the_allowed_folders(server, tmp_path, monkeypatch):
    f = write(tmp_path / "n.json", *grid2d())
    monkeypatch.setenv("MECHANICAL_MCP_ROOTS", str(tmp_path / "elsewhere"))
    out = J(server.linearize_path(nodal_file=f, start_mm="5,0", end_mm="5,20"))
    assert out["ok"] is False and "outside" in out["error"]


def test_tool_with_a_garbage_json_file(server, tmp_path):
    f = tmp_path / "g.json"
    f.write_text("{not json")
    out = J(server.linearize_path(nodal_file=str(f), start_mm="0,0", end_mm="1,1"))
    assert out["ok"] is False


# -- gaps, size and time limits, components ---------------------------------------------------------------


def grid3d(n=14, hole=None):
    pts, vals = [], []
    for x in range(n):
        for y in range(n):
            for z in range(n):
                if hole and hole(x, y, z):
                    continue
                pts.append([float(x), float(y), float(z)])
                vals.append(2.0 * z)
    return pts, vals


def test_a_line_through_a_hole_is_refused(tmp_path):
    """LinearNDInterpolator happily interpolates across a void inside the convex hull."""
    pts, vals = grid3d(hole=lambda x, y, z: abs(x - 7) < 3 and abs(y - 7) < 3 and 4 < z < 10)
    f = write(tmp_path / "n.json", pts, vals)
    with pytest.raises(ValueError, match="gap with no nodes"):
        pp.linearize_file(f, (7.0, 7.0, 0.0), (7.0, 7.0, 13.0))
    r = pp.linearize_file(f, (7.0, 7.0, 0.0), (7.0, 7.0, 13.0), allow_gaps=True)
    assert r["interpolated_across_gap"] is True and r["gap_spans_mm"]
    lo, hi = r["gap_spans_mm"][0]
    assert 3.0 < lo < 6.0 and 8.0 < hi < 11.0


def test_a_line_beside_the_hole_is_fine(tmp_path):
    pts, vals = grid3d(hole=lambda x, y, z: abs(x - 7) < 3 and abs(y - 7) < 3 and 4 < z < 10)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (1.0, 1.0, 0.0), (1.0, 1.0, 13.0))
    assert r["interpolated_across_gap"] is False and r["membrane"] == pytest.approx(13.0)


def test_a_notch_in_a_2d_section_is_detected(tmp_path):
    pts, vals = grid2d(nx=21, ny=21, f=lambda x, y: 5.0 * y)
    keep = [i for i, p in enumerate(pts) if not (8 <= p[0] <= 12 and 8 <= p[1] <= 12)]
    f = write(tmp_path / "n.json", [pts[i] for i in keep], [vals[i] for i in keep])
    with pytest.raises(ValueError, match="gap"):
        pp.linearize_file(f, (10, 0), (10, 20), "xy")


def test_a_large_export_is_fast_because_only_nearby_nodes_are_triangulated(tmp_path):
    import time

    pts, vals = grid3d(n=40)  # 64000 nodes
    f = write(tmp_path / "n.json", pts, vals)
    t = time.time()
    r = pp.linearize_file(f, (20.0, 20.0, 0.0), (20.0, 20.0, 39.0))
    assert time.time() - t < 15 and r["membrane"] == pytest.approx(39.0)


def test_node_cap(tmp_path, monkeypatch):
    f = write(tmp_path / "n.json", *grid2d())
    monkeypatch.setattr(pp, "MAX_NODES", 100)
    with pytest.raises(ValueError, match="MECHANICAL_MCP_MAX_NODES"):
        pp.linearize_file(f, (5, 0), (5, 20), "xy")


def test_time_budget(server, tmp_path, monkeypatch):
    import time

    f = write(tmp_path / "n.json", *grid2d())
    monkeypatch.setattr(server, "LINEARIZE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pp, "linearize_file", lambda *a, cancel=None, **k: cancel.wait(5))
    t = time.time()
    out = J(server.linearize_path(nodal_file=f, start_mm="5,0", end_mm="5,20"))
    assert out["ok"] is False and "did not finish" in out["error"] and time.time() - t < 3


def test_component_check_and_echo(tmp_path):
    f = write(tmp_path / "n.json", *grid2d(), orientation="YAxis")
    r = pp.linearize_file(f, (5, 0), (5, 20), "xy", expect_component="y")
    assert r["component"] == "YAxis" and "single exported scalar" in r["linearized_quantity"]
    with pytest.raises(ValueError, match="holds the YAxis component"):
        pp.linearize_file(f, (5, 0), (5, 20), "xy", expect_component="x")
    with pytest.raises(ValueError):
        pp.linearize_file(f, (5, 0), (5, 20), "xy", expect_component="w")
    g = write(tmp_path / "m.json", *grid2d())
    assert pp.linearize_file(g, (5, 0), (5, 20), "xy")["component"].startswith("not recorded")


def test_tool_reports_gaps_and_component_errors(server, tmp_path):
    pts, vals = grid3d(hole=lambda x, y, z: abs(x - 7) < 3 and abs(y - 7) < 3 and 4 < z < 10)
    f = write(tmp_path / "n.json", pts, vals, orientation="ZAxis")
    out = J(server.linearize_path(nodal_file=f, start_mm="7,7,0", end_mm="7,7,13"))
    assert out["ok"] is False and "gap" in out["error"]
    out = J(server.linearize_path(nodal_file=f, start_mm="7,7,0", end_mm="7,7,13", allow_gaps=True))
    assert out["ok"] is True and out["interpolated_across_gap"] is True
    out = J(
        server.linearize_path(nodal_file=f, start_mm="1,1,0", end_mm="1,1,13", expect_component="x")
    )
    assert out["ok"] is False and "component" in out["error"]
