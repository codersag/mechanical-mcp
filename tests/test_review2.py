"""Second review round: dependents, new tools, idempotency, exports, listings, survivors."""

import json
import os

import pytest
from fake_mech import Obj, Q, Sel

import mechanical_scripts as ms


def J(s):
    return json.loads(s)


@pytest.fixture
def geo(mech):
    fake, s = mech
    b1 = fake.add_body("Plate", part="PartA")
    b2 = fake.add_body("Block", part="PartB")
    fake.add_face(b1, 11, (0.1, 0, 0), half=0.01)
    fake.add_face(b1, 12, (0.3, 0, 0), half=0.01)
    fake.add_face(
        b2, 21, (0.5, 0, 0), half=0.2
    )  # a big face whose centroid is inside, body sticks out
    b1._geo.Edges.append(Obj("e", Id=31, Centroid=[0, 0, 0]))
    fake.build_geo()
    fake.add_ns("Base", [11, 12])
    return fake, s


# -- 7. delete_named_selection and its dependents ---------------------------------------------------------


def test_delete_refuses_while_loads_are_scoped_to_it(geo):
    fake, s = geo
    assert J(s.add_force("Base", fx_n=1.0))["ok"] is True
    out = J(s.delete_named_selection("Base"))
    assert out["ok"] is False and out["dependents"][0]["type"] == "Force"
    assert len(fake.model.NamedSelections) == 1


def test_delete_lists_every_kind_of_dependent(geo):
    fake, s = geo
    s.add_force("Base", fx_n=1.0)
    s.add_equivalent_stress("Base")
    pm = Obj("PM", Location=fake.model.NamedSelections[0])
    fake.model.Geometry.PointMasses.append(pm)
    sz = fake.model.Mesh.AddSizing()
    sz.Location = fake.model.NamedSelections[0]
    out = J(s.delete_named_selection("Base"))
    kinds = {d["type"] for d in out["dependents"]}
    assert out["ok"] is False and {"Force", "EquivalentStress", "Obj", "Sizing"} & kinds
    assert len(out["dependents"]) == 4


def test_force_true_deletes_and_reports_the_orphans(geo):
    fake, s = geo
    s.add_force("Base", fx_n=1.0)
    out = J(s.delete_named_selection("Base", force=True))
    assert out["ok"] is True and out["deleted_count"] == 1 and out["orphaned"][0]["name"] == "Force"
    assert fake.model.NamedSelections == []


def test_delete_without_dependents_just_works(geo):
    fake, s = geo
    out = J(s.delete_named_selection("Base"))
    assert out["ok"] is True and out["orphaned"] == []


def test_two_selections_with_one_name_are_both_deleted_and_counted(geo):
    fake, s = geo
    fake.add_ns("Base", [11])
    out = J(s.delete_named_selection("Base"))
    assert out["ok"] is True and out["deleted_count"] == 2 and fake.model.NamedSelections == []


def test_dependents_of_the_second_duplicate_are_found_too(geo):
    fake, s = geo
    second = fake.add_ns("Base", [11])
    f = fake.static.AddForce()
    f.Location = second
    out = J(s.delete_named_selection("Base"))
    assert out["ok"] is False and len(fake.model.NamedSelections) == 2


# -- 12. create_named_selection -----------------------------------------------------------------------------


def test_create_from_body_names_and_prefix(geo):
    fake, s = geo
    out = J(s.create_named_selection("bodies", body_names="Plate,Block"))
    assert out["ok"] is True and out["entities"] == 2 and out["entity_type"] == "body"
    out = J(s.create_named_selection("pre", body_prefix="Pl"))
    assert out["entities"] == 1
    ns = [n for n in fake.model.NamedSelections if n.Name == "pre"][0]
    assert ns.Location.Ids == [101]


def test_create_from_ids_and_boxes_reports_counts_and_warnings(geo):
    fake, s = geo
    out = J(s.create_named_selection("ids", entity_ids="11,12"))
    assert out["ok"] is True and out["entities"] == 2
    out = J(s.create_named_selection("box", box_mm="0,600,,,,"))
    assert out["entities"] == 3 and any("CENTROID" in w for w in out["warnings"])
    out = J(s.create_named_selection("boxfull", box_mm="0,600,,,,", fully_inside=True))
    assert out["entities"] == 2 and out["warnings"] == []


def test_create_refuses_empty_results_and_bad_arguments(geo):
    fake, s = geo
    before = len(fake.model.NamedSelections)
    for kw in (
        {"body_prefix": "zzz"},
        {"body_names": "nothere"},
        {"entity_ids": "999"},
        {"box_mm": "900,901,,,,"},
        {},
        {"body_names": "Plate", "body_prefix": "x"},
        {"box_mm": "0,1,,,,", "entity_type": "solid"},
    ):
        assert J(s.create_named_selection("n", **kw))["ok"] is False, kw
    assert J(s.create_named_selection("", body_names="Plate"))["ok"] is False
    assert len(fake.model.NamedSelections) == before


def test_create_refuses_a_duplicate_name_unless_replace(geo):
    fake, s = geo
    out = J(s.create_named_selection("Base", body_names="Plate"))
    assert out["ok"] is False and "replace=true" in out["error"]
    out = J(s.create_named_selection("Base", body_names="Plate", replace=True))
    assert out["ok"] is True and out["replaced"] == 1
    assert [n.Name for n in fake.model.NamedSelections] == ["Base"]
    assert fake.model.NamedSelections[0].Location.Ids == [101]


def test_replace_is_refused_when_loads_depend_on_the_old_one(geo):
    fake, s = geo
    s.add_force("Base", fx_n=1.0)
    out = J(s.create_named_selection("Base", body_names="Plate", replace=True))
    assert out["ok"] is False and out["dependents"]
    assert len(fake.model.NamedSelections) == 1 and fake.model.NamedSelections[0].Location.Ids == [
        11,
        12,
    ]


def test_create_rolls_back_when_a_step_fails(geo):
    fake, s = geo
    orig = fake.model.AddNamedSelection

    def broken():
        ns = orig()
        ns._fail["Location"] = RuntimeError("cannot set")
        return ns

    fake.model.AddNamedSelection = broken
    before = len(fake.model.NamedSelections)
    out = J(s.create_named_selection("x", body_names="Plate"))
    assert out["ok"] is False and out["rolled_back"] is True
    assert len(fake.model.NamedSelections) == before


def test_replace_failure_keeps_the_old_selection(geo):
    fake, s = geo
    orig = fake.model.AddNamedSelection

    def broken():
        ns = orig()
        ns._fail["Name"] = RuntimeError("cannot rename")
        return ns

    fake.model.AddNamedSelection = broken
    out = J(s.create_named_selection("Base", body_names="Plate", replace=True))
    assert out["ok"] is False
    assert [n.Name for n in fake.model.NamedSelections] == ["Base"]
    assert fake.model.NamedSelections[0].Location.Ids == [11, 12]


# -- 12. listings ----------------------------------------------------------------------------------------------


def test_list_boundary_conditions_shows_loads_scope_and_not_settings(geo):
    fake, s = geo
    s.add_force("Base", fx_n=1.0)
    s.add_standard_gravity()
    s.add_fixed_support("", box_mm="0,200,,,,")
    # the things the old filter let through
    for name, typ in (
        ("Analysis Settings", "ANSYSAnalysisSettings"),
        ("Initial Conditions", "DataModelObject"),
        ("Commands", "CommandSnippet"),
        ("Solution", "Solution"),
    ):
        fake.static.Children.append(Obj(name, _type=typ))
    out = J(s.list_boundary_conditions(0))
    rows = {r["name"]: r for r in out["boundary_conditions"]}
    assert set(rows) == {"Force", "Standard Earth Gravity", "Fixed Support"}
    assert rows["Force"]["scope_named_selection"] == "Base" and rows["Force"]["scope_entities"] == 2
    assert rows["Fixed Support"]["scope_entities"] == 1
    assert "scope_named_selection" not in rows["Fixed Support"]  # a geometry scope has no name
    assert out["analysis_type"] == "Static"


def test_report_lists_loads_with_scope(geo, tmp_path):
    fake, s = geo
    s.add_force("Base", fx_n=1.0)
    fake.static.Children.append(Obj("Analysis Settings", _type="ANSYSAnalysisSettings"))
    out = J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt"))
    assert out["ok"] is True
    text = (tmp_path / "r.txt").read_text(encoding="utf-8")
    body = text.split("BOUNDARY CONDITIONS")[1].split("RESULTS")[0]
    assert "Force (Force)" in body and "scope: Base [2 entities]" in body
    assert "Analysis Settings" not in body and "Solution" not in body


def test_list_contact_regions(mech):
    fake, s = mech
    a = fake.model.Connections.AddContactRegion()
    a.SourceLocation, a.TargetLocation = Sel(), Sel()
    a.SourceLocation.Ids, a.TargetLocation.Ids = [1, 2], [3]
    a.FrictionCoefficient = 0.2
    fake.model.Connections.AddContactRegion()
    out = J(s.list_contact_regions())
    assert out["ok"] is True and len(out["contact_regions"]) == 2
    first = out["contact_regions"][0]
    assert first["index"] == 0 and first["source_entities"] == 2 and first["target_entities"] == 1
    assert first["friction_coefficient"] == 0.2
    assert out["duplicate_names"] == []
    fake.model.ContactRegions[1].Name = "Contact Region"
    assert J(s.list_contact_regions())["duplicate_names"] == ["Contact Region"]


def test_list_results_and_get_result_values(mech):
    fake, s = mech
    s.add_equivalent_stress()
    s.add_normal_stress("y")
    out = J(s.list_results(0))
    assert (
        out["solved"] is False
        and out["note"]
        and [r["max"] for r in out["results"]] == [None, None]
    )
    fake.static.Solution.Status = "Done"
    out = J(s.list_results(0))
    r0 = out["results"][0]
    assert r0["max"] == "12.5 [Pa]" and r0["max_value"] == 12.5 and r0["unit"] == "Pa"
    assert "NormalOrientation" not in r0  # only for normal / directional results
    one = J(s.get_result_values("Equivalent Stress", 0))
    assert one["ok"] is True and one["max_value"] == 12.5 and one["min_value"] == -3.0
    assert J(s.get_result_values("nothere", 0))["ok"] is False
    pr = fake.static.Solution.AddForceReaction()
    pr.Name = "Probe"
    rx = J(s.get_result_values("Probe", 0))
    assert rx["x"] == "1 [N]" and rx["total"] == "4 [N]"


def test_new_tools_survive_hostile_strings(mech):
    fake, s = mech
    for h in ('quo"te', "back\\slash", "new\nline", "café", "\U0001f600"):
        s.create_named_selection(h, body_names=h)
        s.get_result_values(h, 0)
        s.delete_named_selection(h, True)
    for sc in fake.scripts:
        compile(sc, "<g>", "exec")
        assert sc.isascii()


# -- 8. idempotency ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.add_equivalent_stress(),
        lambda s: s.add_total_deformation(),
        lambda s: s.add_principal_stress("max"),
        lambda s: s.add_directional_deformation("Y"),
        lambda s: s.add_normal_stress("x"),
        lambda s: s.add_stress_intensity(),
        lambda s: s.add_stress_tool(),
    ],
)
def test_asking_twice_returns_the_existing_result(mech, call):
    fake, s = mech
    first = J(call(s))
    n = len(fake.static.Solution.Children)
    second = J(call(s))
    assert first["created"] is True and first["already_exists"] is False
    assert (
        second["created"] is False
        and second["already_exists"] is True
        and second["name"] == first["name"]
    )
    assert len(fake.static.Solution.Children) == n


def test_different_settings_or_scope_are_not_duplicates(geo):
    fake, s = geo
    assert J(s.add_normal_stress("x"))["created"] is True
    assert J(s.add_normal_stress("y"))["created"] is True
    assert J(s.add_equivalent_stress())["created"] is True
    assert J(s.add_equivalent_stress("Base"))["created"] is True
    assert J(s.add_equivalent_stress("Base"))["created"] is False
    assert J(s.add_directional_deformation("X"))["created"] is True
    assert J(s.add_directional_deformation("Z"))["created"] is True


def test_reuse_existing_false_allows_a_duplicate(mech):
    fake, s = mech
    s.add_equivalent_stress()
    out = J(s.add_equivalent_stress("", 0, False))
    assert out["created"] is True and len(fake.static.Solution.Children) == 2


def test_reaction_probe_is_not_duplicated(geo):
    fake, s = geo
    s.add_reaction_force("Base")
    out = J(s.add_reaction_force("Base"))
    assert out["already_exists"] is True and len(fake.static.Solution.Children) == 1


def test_a_failure_after_reuse_does_not_delete_the_existing_result(mech):
    fake, s = mech
    s.add_equivalent_stress()
    fake.static.Solution.Status = "Done"

    def boom():
        raise RuntimeError("evaluation failed")

    fake.static.Solution.EvaluateAllResults = boom
    out = J(s.add_equivalent_stress())
    assert out["ok"] is False and len(fake.static.Solution.Children) == 1  # the original survives


def test_modal_modes_are_distinct_results(mech):
    fake, s = mech
    assert J(s.add_total_deformation(1, 1))["created"] is True
    assert J(s.add_total_deformation(2, 1))["created"] is True
    assert J(s.add_total_deformation(2, 1))["created"] is False


def test_add_contact_twice_reports_the_existing_region(geo):
    fake, s = geo
    first = J(s.add_contact("bonded", source_entity_ids="11", target_entity_ids="21"))
    second = J(s.add_contact("bonded", source_entity_ids="11", target_entity_ids="21"))
    assert (
        first["created"] is True
        and second["already_exists"] is True
        and len(fake.model.ContactRegions) == 1
    )
    third = J(s.add_contact("rough", source_entity_ids="11", target_entity_ids="21"))
    assert (
        third["ok"] is False and "differs" in third["error"] and len(fake.model.ContactRegions) == 1
    )
    fourth = J(s.add_contact("bonded", source_entity_ids="12", target_entity_ids="21"))
    assert fourth["created"] is True and len(fake.model.ContactRegions) == 2
    again = J(
        s.add_contact(
            "bonded", source_entity_ids="11", target_entity_ids="21", reuse_existing=False
        )
    )
    assert again["created"] is True


# -- 8. set_contact_type ---------------------------------------------------------------------------------------------


def test_set_contact_type_counts_only_real_changes(mech):
    fake, s = mech
    for _ in range(3):
        fake.model.Connections.AddContactRegion()
    fake.model.ContactRegions[0].ContactType = "Rough"
    out = J(s.set_contact_type("bonded"))
    assert out["regions_changed"] == 1 and out["regions_unchanged"] == 2
    assert out["changed"][0]["name"] == "Contact Region" and out["changed"][0]["was"] == "Rough"
    assert out["changed"][0]["now"] == "Bonded"
    again = J(s.set_contact_type("bonded"))
    assert again["ok"] is True and again["regions_changed"] == 0 and again["regions_unchanged"] == 3


def test_set_contact_type_warns_about_duplicate_names(mech):
    fake, s = mech
    fake.model.Connections.AddContactRegion()
    fake.model.Connections.AddContactRegion().Name = "Contact Region"
    out = J(s.set_contact_type("frictionless", region_names="Contact Region"))
    assert (
        out["regions_changed"] == 2
        and "2 contact regions are named Contact Region" in out["warnings"][0]
    )


def test_set_contact_type_restores_everything_when_one_region_fails(mech):
    fake, s = mech
    for _ in range(3):
        fake.model.Connections.AddContactRegion()
    fake.model.ContactRegions[2]._fail["ContactType"] = RuntimeError("rejected")
    out = J(s.set_contact_type("rough"))
    assert out["ok"] is False and out["restored"] is True
    assert [str(r.ContactType) for r in fake.model.ContactRegions] == ["Bonded"] * 3


def test_set_contact_type_readback_mismatch_restores(mech):
    fake, s = mech
    for _ in range(2):
        fake.model.Connections.AddContactRegion()
    reg = fake.model.ContactRegions[1]
    object.__setattr__(reg, "ContactType", "Bonded")
    cls = type(
        "Sticky", (Obj,), {"ContactType": property(lambda self: "Bonded", lambda self, v: None)}
    )
    reg.__class__ = cls
    out = J(s.set_contact_type("rough"))
    assert out["ok"] is False and "after setting it" in out["error"]


# -- survivors: restore and rollback paths ---------------------------------------------------------------------------------


def test_suppress_restores_the_bodies_already_changed(geo):
    fake, s = geo
    second = fake.model.Geometry.Bodies[1]
    second._fail["Suppressed"] = RuntimeError("cannot suppress")
    out = J(s.suppress_bodies("", all_bodies=True))
    assert out["ok"] is False and out["restored"] is True
    assert not any(b.Suppressed for b in fake.model.Geometry.Bodies)


def test_suppress_that_does_not_stick_is_an_error(geo):
    fake, s = geo
    body = fake.model.Geometry.Bodies[0]
    body.__class__ = type(
        "Stuck", (Obj,), {"Suppressed": property(lambda self: False, lambda self, v: None)}
    )
    out = J(s.suppress_bodies("Plate"))
    assert out["ok"] is False and "did not change state" in out["error"]


def test_body_sizing_update_failure_restores_size_and_scope(geo):
    fake, s = geo
    assert J(s.set_body_mesh_size("Plate", 2.0))["created"] is True
    sz = fake.model.Mesh.Sizings[0]
    ids_before = list(sz.Location.Ids)
    size_before = sz.ElementSize
    sz._fail["Location"] = RuntimeError("cannot rescope")
    out = J(s.set_body_mesh_size("Plate", 5.0))
    assert out["ok"] is False and out["restored"] is True
    assert sz.ElementSize is size_before and list(sz.Location.Ids) == ids_before


def test_body_sizing_creation_failure_removes_the_new_control(geo):
    fake, s = geo
    orig = fake.model.Mesh.AddSizing

    def broken():
        z = orig()
        z._fail["ElementSize"] = RuntimeError("bad size")
        return z

    fake.model.Mesh.AddSizing = broken
    out = J(s.set_body_mesh_size("Plate", 2.0))
    assert out["ok"] is False and fake.model.Mesh.Sizings == []


def test_element_size_is_restored_when_mechanical_ignores_it(mech):
    fake, s = mech
    mesh = fake.model.Mesh
    old = Q(0.01, "m")
    history = []
    state = {"v": old}

    def get(self):
        return (
            Q(0.0, "m")
            if state["v"] is not old and abs(state["v"].si - 0.005) < 1e-12
            else state["v"]
        )

    def put(self, v):
        history.append(v)
        state["v"] = v

    mesh.__class__ = type("M", (Obj,), {"ElementSize": property(get, put)})
    out = J(s.set_mesh_element_size(5.0))
    assert out["ok"] is False and "did not accept" in out["error"]
    assert history[-1] is old and state["v"] is old  # the previous size object was put back


def test_all_modes_rolls_back_every_created_result(mech):
    fake, s = mech
    fake.modal.Solution.Status = "Done"
    count = {"n": 0}

    def hook(o):
        count["n"] += 1
        if count["n"] == 3:
            o._fail["Mode"] = RuntimeError("mode rejected")

    fake.result_hooks = [hook]
    out = J(s.add_total_deformation_all_modes(4, 1))
    assert out["ok"] is False and out["rolled_back"] is True
    assert fake.modal.Solution.Children == []


def test_a_load_that_ends_up_with_an_empty_scope_is_rolled_back(geo):
    fake, s = geo

    def hook(o):
        empty = Sel()
        empty.Ids = []
        o.__class__ = type(
            "E", (Obj,), {"Location": property(lambda self: empty, lambda self, v: None)}
        )

    fake.load_hooks = [hook]
    out = J(s.add_force("Base", fx_n=1.0))
    assert out["ok"] is False and "empty scope" in out["error"] and fake.static.Children == []


def test_a_reaction_probe_with_an_empty_scope_is_rolled_back(geo):
    fake, s = geo

    def hook(o):
        empty = Sel()
        empty.Ids = []
        o.__class__ = type(
            "E", (Obj,), {"GeometryLocation": property(lambda self: empty, lambda self, v: None)}
        )

    fake.result_hooks = [hook]
    out = J(s.add_reaction_force("Base"))
    assert (
        out["ok"] is False and "empty scope" in out["error"] and fake.static.Solution.Children == []
    )


def test_assign_material_readback_mismatch_restores(geo):
    fake, s = geo
    b = fake.model.Geometry.Bodies[1]
    b.__class__ = type(
        "S", (Obj,), {"Material": property(lambda self: "Structural Steel", lambda self, v: None)}
    )
    object.__setattr__(fake.model.Geometry.Bodies[0], "Material", "Structural Steel")
    out = J(s.assign_material("Block", "Q235"))
    assert out["ok"] is False and "after assignment" in out["error"]


def test_gravity_off_axis_by_a_hair_is_not_snapped_to_an_axis():
    with pytest.raises(ValueError):
        ms.gravity_direction(1.0, 1e-6, 0.0)
    assert ms.gravity_direction(1.0, 1e-12, 0.0)[0] == "PositiveXAxis"


def test_json_helper_makes_nan_and_infinity_valid_json(server):
    out = server._json(
        {"a": float("nan"), "b": [float("inf"), -float("inf")], "c": {"d": float("nan")}}
    )
    doc = json.loads(out, parse_constant=lambda c: pytest.fail("bare %s in output" % c))
    assert doc == {"a": "NaN", "b": ["Infinity", "-Infinity"], "c": {"d": "NaN"}}


def test_wait_must_be_a_real_boolean(mech):
    fake, s = mech
    for bad in ("yes", 1, None, "false"):
        out = J(s.solve_analysis(0, bad))
        assert out["ok"] is False and "wait" in out["error"]
    assert fake.scripts == []


# -- survivors: file guards --------------------------------------------------------------------------------------------------


def test_hard_linked_files_are_not_overwritten(server, tmp_path):
    target = tmp_path / "t.json"
    target.write_text("{}")
    link = tmp_path / "l.json"
    os.link(target, link)
    p, err = server._allow(link, exts={".json"}, write=True, overwrite=True)
    assert p is None and "hard-linked" in json.loads(err)["error"]


def test_symbolic_links_are_not_written_through(server, tmp_path, monkeypatch):
    from pathlib import Path

    target = tmp_path / "t.json"
    target.write_text("{}")
    link = tmp_path / "l.json"
    real = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda self: True if self.name == "l.json" else real(self)
    )
    p, err = server._allow(link, exts={".json"}, write=True, overwrite=True)
    assert p is None and "symbolic link" in json.loads(err)["error"]


# -- 10. export: overwrite, links, temp files, units -------------------------------------------------------------------------------


def _export_fake(fake, values=(10.0, 20.0, 30.0), maximum=30.0, unit="Pa", orientation=None):
    fake.static.Solution.Status = "Done"
    res = fake.static.Solution.AddNormalStress()
    res.Name = "Normal Stress"
    res.PlotData = {"Node": [1, 2, 3], "Values": list(values)}
    res.EvaluateAllResults = lambda: None
    res.Maximum = Q(maximum, unit)
    if orientation:
        res.NormalOrientation = orientation
    nd = {i: Obj("n", X=0.001 * i, Y=0.0, Z=0.0) for i in (1, 2, 3)}
    fake.ext.DataModel.MeshDataByName = lambda n: Obj("md", Unit="m", NodeById=lambda i: nd[i])
    return res


def test_the_script_itself_refuses_to_overwrite(mech, tmp_path):
    fake, s = mech
    _export_fake(fake)
    dest = tmp_path / "n.json"
    dest.write_text("PRECIOUS")
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, False)))
    assert out["ok"] is False and "exists" in out["error"] and dest.read_text() == "PRECIOUS"
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, True)))
    assert out["ok"] is True and json.loads(dest.read_text())["values"] == [10.0, 20.0, 30.0]
    assert [p.name for p in tmp_path.iterdir()] == ["n.json"]


def test_the_script_never_follows_or_removes_other_files(mech, tmp_path):
    fake, s = mech
    _export_fake(fake)
    dest = tmp_path / "n.json"
    unrelated = tmp_path / "n.json.part"
    unrelated.write_text("MINE")
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(dest), 0, False)))
    assert out["ok"] is True and unrelated.read_text() == "MINE"
    # a directory where the file should go is refused, not removed
    d = tmp_path / "dir.json"
    d.mkdir()
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(d), 0, True)))
    assert out["ok"] is False and d.is_dir()


def test_a_failed_export_removes_only_its_own_temporary(mech, tmp_path):
    fake, s = mech
    res = _export_fake(fake)
    res.PlotData = {"Node": [1, 2, 3], "Values": [1.0, "x", 3.0]}
    unrelated = tmp_path / "n.json.part"
    unrelated.write_text("MINE")
    out = J(fake.run(ms.export_nodal_script("Normal Stress", str(tmp_path / "n.json"), 0, False)))
    assert (
        out["ok"] is False
        and unrelated.read_text() == "MINE"
        and not (tmp_path / "n.json").exists()
    )


def test_export_states_whether_the_value_unit_was_cross_checked(mech, tmp_path):
    fake, s = mech
    _export_fake(fake, values=(10.0, 20.0, 30.0), maximum=30.0)
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "a.json"), 0))
    assert out["value_unit"] == "Pa" and out["value_unit_verified"] is True
    doc = json.loads((tmp_path / "a.json").read_text())
    assert doc["value_unit_verified"] is True
    fake.static.Solution.Children.clear()
    _export_fake(
        fake, values=(10.0, 20.0, 30.0), maximum=30000.0, unit="kPa"
    )  # PlotData in another unit
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "b.json"), 0))
    assert out["ok"] is True and out["value_unit_verified"] is False
    assert out["max_exported"] == 30.0 and out["result_maximum"] == 30000.0


def test_export_records_the_component(mech, tmp_path):
    fake, s = mech
    _export_fake(fake, orientation="YAxis")
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "c.json"), 0))
    assert out["orientation"] == "YAxis"
    assert json.loads((tmp_path / "c.json").read_text())["orientation"] == "YAxis"


def test_overwrite_flag_reaches_the_script(mech, tmp_path):
    fake, s = mech
    _export_fake(fake)
    (tmp_path / "n.json").write_text("{}")
    assert J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))["ok"] is False
    assert (
        J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0, True))["ok"] is True
    )
    assert "_OVERWRITE = True" in fake.scripts[-1]


# -- 10. report files --------------------------------------------------------------------------------------------------------------------


def test_report_never_touches_an_unrelated_part_file(mech, tmp_path):
    fake, s = mech
    (tmp_path / "r.txt.part").write_text("MINE")
    assert J(s.generate_report(str(tmp_path / "r.txt"), 0, "txt"))["ok"] is True
    assert (tmp_path / "r.txt.part").read_text() == "MINE"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r.txt", "r.txt.part"]


def test_publish_never_replaces_a_file_that_appeared_meanwhile(server, tmp_path):
    from pathlib import Path

    tmp = tmp_path / "x.tmp"
    tmp.write_text("new")
    dest = tmp_path / "d.txt"
    dest.write_text("created by someone else")
    with pytest.raises(FileExistsError):
        server._publish(Path(tmp), Path(dest), overwrite=False)
    assert dest.read_text() == "created by someone else"
    server._publish(Path(tmp), Path(dest), overwrite=True)
    assert dest.read_text() == "new"


# -- 8. box_mm -----------------------------------------------------------------------------------------------------------------------------


def test_box_centroid_selection_warns_when_entities_stick_out(geo):
    fake, s = geo
    out = J(s.add_fixed_support("", box_mm="0,600,,,,"))
    assert out["scope_entities"] == 3 and "CENTROID" in out["warnings"][0]
    assert out["warnings"][0].startswith("1 of 3")
    out = J(s.add_force("", fx_n=1.0, box_mm="0,600,,,,", fully_inside=True))
    assert out["scope_entities"] == 2 and out["warnings"] == []


def test_fully_inside_needs_a_box(geo):
    fake, s = geo
    assert J(s.add_fixed_support("Base", fully_inside=True))["ok"] is False
    with pytest.raises(ValueError):
        ms.location_snippet(entity_ids="1", fully_inside=True)


def test_every_box_tool_documents_the_centroid_rule(server):
    import asyncio

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in (
        "add_fixed_support",
        "add_frictionless_support",
        "add_force",
        "add_pressure",
        "add_displacement",
        "add_contact",
        "create_named_selection",
    ):
        assert "CENTROID" in tools[name].description, name


# -- 8. explicit analyses --------------------------------------------------------------------------------------------------------------------


def test_explicit_displacement_tables_are_read_back_by_their_last_entry(geo):
    """Explicit Dynamics displacements are time tables [0, value]: the first entry is 0 m."""
    fake, s = geo

    def hook(o):
        comp = o.XComponent

        class Table:
            DiscreteValues = None

            def SetDiscreteValue(self, i, q):
                raise RuntimeError("Object reference not set to an instance of an object.")

        t = Table()

        class Out:
            def SetDiscreteValue(self_inner, i, q):
                t.SetDiscreteValue(i, q)

            @property
            def DiscreteValues(self_inner):
                return t.DiscreteValues

            @DiscreteValues.setter
            def DiscreteValues(self_inner, v):
                t.DiscreteValues = [Q(0.0, "m")] + list(v)

        comp.Output = Out()

    fake.load_hooks = [hook]
    out = J(s.add_displacement("Base", x_mm=1.5))
    assert out["ok"] is True, out


def test_nonlinear_settings_on_an_analysis_without_them(mech):
    fake, s = mech
    fake.static.AnalysisSettings = None
    out = J(s.set_nonlinear_settings(0, large_deflection=True))
    assert out["ok"] is False and "no nonlinear AnalysisSettings" in out["error"]
    fake.static.AnalysisSettings = Obj("S", NumberOfSteps=1)
    out = J(s.set_nonlinear_settings(0, large_deflection=True))
    assert out["ok"] is False and "not available" in out["error"]


def test_body_sizing_update_that_mechanical_rejects_puts_the_old_size_back(geo):
    fake, s = geo
    assert J(s.set_body_mesh_size("Plate", 2.0))["created"] is True
    sz = fake.model.Mesh.Sizings[0]
    original = sz.ElementSize
    history = []
    state = {"v": original}

    def get(self):
        return state["v"] if state["v"] is original else Q(0.0, "m")  # ignores any new size

    def put(self, v):
        history.append(v)
        state["v"] = v

    sz.__class__ = type("Z", (Obj,), {"ElementSize": property(get, put)})
    out = J(s.set_body_mesh_size("Plate", 5.0))
    assert out["ok"] is False and out["restored"] is True
    assert history[-1] is original and state["v"] is original
