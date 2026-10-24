import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mechanical_scripts as ms  # noqa: E402


class _Ent:
    def __init__(self, i, c):
        self.Id, self.Centroid = i, c


class _Vert:
    def __init__(self, i, x, y, z):
        self.Id, self.X, self.Y, self.Z = i, x, y, z


class _Body:
    def __init__(self, name, faces=(), edges=(), verts=()):
        self.Name, self.Faces, self.Edges, self.Vertices = (
            name,
            list(faces),
            list(edges),
            list(verts),
        )


class _Sel:
    Ids = None


def _env(bodies, unit="m"):
    class NS:
        pass

    ns = NS()
    ns.ExtAPI = NS()
    ns.ExtAPI.DataModel = NS()
    ns.ExtAPI.DataModel.GeoData = NS()
    ns.ExtAPI.DataModel.GeoData.Unit = unit
    part = NS()
    part.Bodies = bodies
    asm = NS()
    asm.Parts = [part]
    ns.ExtAPI.DataModel.GeoData.Assemblies = [asm]
    ns.ExtAPI.SelectionManager = NS()
    ns.ExtAPI.SelectionManager.CreateSelectionInfo = lambda kind: _Sel()
    return {"ExtAPI": ns.ExtAPI, "SelectionTypeEnum": NS()}


def _run(snippet, bodies, unit="m"):
    g = _env(bodies, unit)
    g["SelectionTypeEnum"].GeometryEntities = "GE"
    exec(compile(snippet, "snippet", "exec"), g)
    return g


def test_exactly_one_locator_required():
    with pytest.raises(ValueError):
        ms.location_snippet()
    with pytest.raises(ValueError):
        ms.location_snippet(named_selection="a", entity_ids="1")
    with pytest.raises(ValueError):
        ms.location_snippet(entity_ids="1", box_mm="0,1,0,1,0,1")


def test_bad_entity_type_and_ids():
    with pytest.raises(ValueError):
        ms.location_snippet(entity_ids="1", entity_type="solid")
    for bad in ("", "a", "0", "-3"):
        with pytest.raises(ValueError):
            ms.parse_ids(bad if bad else " ")
    assert ms.parse_ids("12, 15 18") == [12, 15, 18]


def test_box_parsing():
    assert ms.parse_box_mm("0,10,,none,-1,1") == [0.0, 10.0, None, None, -1.0, 1.0]
    with pytest.raises(ValueError):
        ms.parse_box_mm("0,1,2")
    with pytest.raises(ValueError):
        ms.parse_box_mm("5,1,0,1,0,1")


def test_named_selection_snippet_quotes_safely():
    s = ms.location_snippet(named_selection='bad"name\nx')
    compile(s, "s", "exec")
    assert '\\"' in s and "\\n" in s and "Named selection not found" in s


def test_ids_snippet_builds_selection():
    b = _Body("p", faces=[_Ent(4, (0, 0, 0))], edges=[_Ent(7, (0, 0, 0))])
    g = _run(ms.location_snippet(entity_ids="4, 7"), [b])
    assert g["_ns"].Ids == [4, 7]


def test_ids_snippet_rejects_ids_that_do_not_exist():
    b = _Body("p", faces=[_Ent(4, (0, 0, 0))])
    g = _run(ms.location_snippet(entity_ids="4, 9"), [b])
    assert g["_ns"] is None and "9" in g["_ns_err"] and "4" not in g["_ns_err"].split(":")[-1]


def test_box_selects_faces_by_centroid_in_metres():
    b = _Body(
        "pipe",
        faces=[_Ent(1, (0.10, 0.0, 0.0)), _Ent(2, (0.30, 0.0, 0.0)), _Ent(3, (0.31, 0.5, 0.0))],
    )
    g = _run(ms.location_snippet(box_mm="250,350,,,,"), [b])
    assert g["_ns"].Ids == [2, 3]
    g = _run(ms.location_snippet(box_mm="250,350,400,600,,"), [b])
    assert g["_ns"].Ids == [3]


def test_box_body_filter_and_edges_and_vertices():
    a = _Body("a", edges=[_Ent(10, (0.0, 0.0, 0.0))], verts=[_Vert(20, 0.0, 0.0, 0.0)])
    b = _Body("b", edges=[_Ent(11, (0.0, 0.0, 0.0))], verts=[_Vert(21, 0.0, 0.0, 0.0)])
    g = _run(ms.location_snippet(box_mm=",,,,,", entity_type="edge", body_name="b"), [a, b])
    assert g["_ns"].Ids == [11]
    g = _run(ms.location_snippet(box_mm="-1,1,-1,1,-1,1", entity_type="vertex"), [a, b])
    assert g["_ns"].Ids == [20, 21]


def test_box_with_no_match_leaves_ns_none():
    b = _Body("p", faces=[_Ent(1, (0.0, 0.0, 0.0))])
    g = _run(ms.location_snippet(box_mm="100,200,,,,"), [b])
    assert g["_ns"] is None and g["_ns_err"] == "No geometry matched the box"


# ---- model setup builders ---------------------------------------------------------------------


def test_2d_behavior():
    s = ms.set_2d_behavior_script("Axisymmetric")
    compile(s, "s", "exec")
    assert "Model2DBehavior.AxiSymmetric" in s
    with pytest.raises(ValueError):
        ms.set_2d_behavior_script("shell")


def test_body_sizing():
    s = ms.body_sizing_script('gasket "A"', 0.5)
    compile(s, "s", "exec")
    assert "AddSizing()" in s and "'m'" in s and '\\"A\\"' in s
    with pytest.raises(ValueError):
        ms.body_sizing_script("", 1.0)
    with pytest.raises(ValueError):
        ms.body_sizing_script("b", 0)


def test_nonlinear_settings():
    s = ms.nonlinear_settings_script(0, True, True, 20, 10, 200, 3)
    compile(s, "s", "exec")
    for needle in (
        "LargeDeflection = True",
        "InitialSubsteps = 20",
        "MinimumSubsteps = 10",
        "MaximumSubsteps = 200",
        "NumberOfSteps = 3",
        "AutomaticTimeStepping.On",
    ):
        assert needle in s
    only = ms.nonlinear_settings_script(0, True)
    assert (
        "LargeDeflection" in only
        and "AutomaticTimeStepping" not in only
        and "_s.InitialSubsteps =" not in only
    )
    with pytest.raises(ValueError):
        ms.nonlinear_settings_script(0, None, True, 5, 10, 200)


def test_contact_type_scripts():
    s = ms.set_contact_type_script("frictional", 0.2)
    compile(s, "s", "exec")
    assert "ContactType.Frictional" in s and "FrictionCoefficient = 0.2" in s
    with pytest.raises(ValueError):
        ms.set_contact_type_script("frictional", 0)
    with pytest.raises(ValueError):
        ms.set_contact_type_script("glued")
    a = ms.location_snippet(entity_ids="3")
    b = ms.location_snippet(box_mm="0,1,,,,")
    c = ms.add_contact_script(a, b, "bonded")
    compile(c, "s", "exec")
    assert "AddContactRegion()" in c and "SourceLocation = _src" in c and "ContactType.Bonded" in c


def test_stress_results():
    s = ms.stress_result_script("normal_stress", "Y")
    compile(s, "s", "exec")
    assert "AddNormalStress()" in s and "NormalOrientationType.YAxis" in s
    t = ms.stress_result_script("stress_intensity")
    compile(t, "s", "exec")
    assert "AddStressIntensity()" in t
    with pytest.raises(ValueError):
        ms.stress_result_script("normal_stress", "w")
    with pytest.raises(ValueError):
        ms.stress_result_script("von_mises")


def test_export_nodal_script_escapes_path():
    s = ms.export_nodal_script("Normal Stress", "C:\\Temp\\out.json")
    compile(s, "s", "exec")
    assert (
        "C:/Temp/out.json" in s
        and "EvaluateAllResults()" in s
        and "NodeById" in s
        and "xyz_mm" in s
    )
    assert "coordinate_unit" in s and "value_unit" in s
    with pytest.raises(ValueError):
        ms.export_nodal_script("", "x")


def test_linearize_linear_profile():
    import mechanical_postproc as pp

    s = [i * 0.005 for i in range(201)]
    v = [100.0 + 40.0 * x for x in s]  # linear through thickness 1.0
    r = pp.linearize(s, v)
    assert abs(r["membrane"] - 120.0) < 1e-9
    assert abs(abs(r["bending"]) - 20.0) < 1e-9


def test_linearize_file_roundtrip(tmp_path):
    import json

    pytest.importorskip("scipy")

    import mechanical_postproc as pp

    pts, vals = [], []
    for x in range(0, 11):
        for y in range(0, 21):
            pts.append([float(x), float(y), 0.0])
            vals.append(100.0 + 5.0 * y)
    f = tmp_path / "n.json"
    f.write_text(json.dumps({"xyz_mm": pts, "values": vals}))
    r = pp.linearize_file(str(f), (5, 0), (5, 20), "xy")
    assert abs(r["membrane"] - 150.0) < 1e-6 and abs(r["section_length_mm"] - 20.0) < 1e-9
    with pytest.raises(ValueError):
        pp.linearize_file(str(f), (50, 50), (60, 60), "xy")


def test_box_honours_geometry_unit_mm():
    b = _Body(
        "p", faces=[_Ent(1, (100.0, 0.0, 0.0)), _Ent(2, (300.0, 0.0, 0.0))]
    )  # centroids in mm
    g = _run(ms.location_snippet(box_mm="250,350,,,,"), [b], unit="mm")
    assert g["_ns"].Ids == [2]


def test_box_rejects_unknown_geometry_unit():
    b = _Body("p", faces=[_Ent(1, (0.0, 0.0, 0.0))])
    with pytest.raises(Exception):
        _run(ms.location_snippet(box_mm="0,1,,,,"), [b], unit="furlong")
