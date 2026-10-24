"""Tool behaviour against a fake Mechanical object model: rollback, status gating, read-back."""

import json

import pytest
from fake_mech import Obj, Q

import mechanical_scripts as ms


def J(s):
    return json.loads(s)


def names(owner):
    return [o.Name for o in owner]


@pytest.fixture
def geo(mech):
    fake, s = mech
    b1 = fake.add_body("Plate", part="PartA")
    b2 = fake.add_body("Plate", part="PartB")  # same name in a different part
    fake.add_face(b1, 11, (0.1, 0, 0))
    fake.add_face(b1, 12, (0.3, 0, 0))
    fake.add_face(b2, 21, (0.5, 0, 0))
    b1._geo.Edges.append(Obj("e", Id=31, Centroid=[0, 0, 0]))
    fake.build_geo()
    fake.add_ns("Base", [11, 12])
    fake.add_ns("Empty", [])
    return fake, s


# -- A1 gravity ----------------------------------------------------------------------------------


def test_gravity_uses_the_method_that_exists_in_2026r1(mech):
    fake, s = mech
    out = J(s.add_standard_gravity(0, 0, -1, 0))
    assert out["ok"] is True and out["direction"] == "NegativeZAxis" or out["ok"]
    assert "AddEarthGravity" in fake.scripts[-1]
    assert "AddStandardEarthGravity" not in fake.scripts[-1]
    assert names(fake.static.Children) == ["Standard Earth Gravity"]


def test_gravity_direction_is_set_from_the_normalised_vector(mech):
    fake, s = mech
    out = J(s.add_standard_gravity(0, 0.0, -5.0, 0.0))
    assert out["ok"] is True
    assert "GravityOrientationType.NegativeYAxis" in fake.scripts[-1]


def test_oblique_gravity_creates_nothing(mech):
    fake, s = mech
    out = J(s.add_standard_gravity(0, 1, 1, 0))
    assert out["ok"] is False and "along" in out["error"]
    assert fake.static.Children == [] and fake.scripts == []


# -- A5 rollback --------------------------------------------------------------------------------


def test_failed_scope_assignment_removes_the_load(geo):
    fake, s = geo

    def hook(o):
        o._fail["Location"] = RuntimeError("cannot scope")

    fake.load_hooks = [hook]
    out = J(s.add_force("Base", fx_n=5.0))
    assert out["ok"] is False and out["rolled_back"] is True and out["created"] is False
    assert fake.static.Children == []
    fake.load_hooks = []
    assert J(s.add_force("Base", fx_n=5.0))["name"] == "Force"  # retry is "Force", not "Force 2"


class _BadOutput:
    def SetDiscreteValue(self, i, q):
        raise RuntimeError("bad value")

    @property
    def DiscreteValues(self):
        return None

    @DiscreteValues.setter
    def DiscreteValues(self, v):
        raise RuntimeError("bad value")


def test_failure_after_configuration_removes_the_load(geo):
    fake, s = geo

    def hook(o):
        o.XComponent.Output = _BadOutput()

    fake.load_hooks = [hook]
    out = J(s.add_force("Base", fx_n=5.0))
    assert out["ok"] is False and "bad value" in out["error"] and fake.static.Children == []


def test_displacement_components_that_start_free_are_set_and_read_back(geo):
    """Live on 2026 R1: SetDiscreteValue on a Free component raised a NullReference."""
    fake, s = geo
    out = J(s.add_displacement("Base", x_mm=2.0, z_mm=0.0))
    assert out["ok"] is True
    d = fake.static.Children[0]
    assert d.XComponent.Output.DiscreteValues[0].Value == pytest.approx(0.002)
    assert d.YComponent.Output.DiscreteValues is None  # not mentioned: stays free
    assert d.ZComponent.Output.DiscreteValues[0].Value == 0.0


def test_remote_displacement_uses_the_real_rotation_property_names(geo):
    fake, s = geo
    out = J(s.add_remote_displacement("Base", x_mm=1.0, rot_x_deg=5.0, rot_z_deg=-2.0))
    assert out["ok"] is True, out
    d = fake.static.Children[0]
    assert (
        d.RotationX.Output.DiscreteValues[0].Value == 5.0
        and d.RotationZ.Output.DiscreteValues[0].Value == -2.0
    )
    assert "XRotation" not in fake.scripts[-1]


def test_a_value_mechanical_did_not_keep_is_an_error(geo):
    fake, s = geo

    class Lossy(_BadOutput):
        def SetDiscreteValue(self, i, q):
            pass

        @property
        def DiscreteValues(self):
            from fake_mech import Q

            return [Q(999.0, "N")]

        @DiscreteValues.setter
        def DiscreteValues(self, v):
            pass

    def hook(o):
        o.XComponent.Output = Lossy()

    fake.load_hooks = [hook]
    out = J(s.add_force("Base", fx_n=5.0))
    assert out["ok"] is False and "instead of" in out["error"] and fake.static.Children == []


def test_rollback_that_cannot_delete_says_so(geo):
    fake, s = geo

    def hook(o):
        o._fail["Location"] = RuntimeError("boom")
        o._fail["Delete"] = RuntimeError("no delete")

    fake.load_hooks = [hook]
    out = J(s.add_pressure("Base", 1e5))
    assert out["ok"] is False and out["rolled_back"] is False and out["created"] is True
    assert "delete it in the tree" in out["error"]


def test_bad_entity_ids_create_nothing(geo):
    fake, s = geo
    out = J(s.add_force("", fx_n=5.0, entity_ids="999"))
    assert out["ok"] is False and "999" in out["error"]
    assert fake.static.Children == []


def test_empty_and_missing_named_selection_create_nothing(geo):
    fake, s = geo
    for tool, kw in (
        (s.add_fixed_support, {}),
        (s.add_frictionless_support, {}),
        (s.add_force, {"fx_n": 1.0}),
        (s.add_pressure, {"magnitude_pa": 1.0}),
        (s.add_displacement, {"x_mm": 1.0}),
        (s.add_remote_displacement, {"x_mm": 1.0}),
        (s.add_remote_force, {"fx_n": 1.0}),
        (s.add_moment, {"mx_nm": 1.0}),
    ):
        for ns in ("Empty", "nothere"):
            out = J(tool(ns, **kw))
            assert out["ok"] is False and out["created"] is False
    assert fake.static.Children == []


def test_good_load_reports_scope_and_name(geo):
    fake, s = geo
    out = J(s.add_fixed_support("Base"))
    assert out == {
        "ok": True,
        "created": True,
        "type": "Fixed Support",
        "name": "Fixed Support",
        "scope_entities": 2,
        "scope_type": "face",
        "analysis": {"index": 0, "name": "Static Structural", "type": "Static"},
        "object_state": "FullyDefined",
        "warnings": [],
    }


def test_ids_lookup_checks_that_entities_exist(geo):
    fake, s = geo
    out = J(s.add_fixed_support("", entity_ids="11,31"))  # a face and an edge
    assert out["ok"] is True and out["scope_entities"] == 2
    out = J(s.add_fixed_support("", entity_ids="11,5555", body_name="PartX"))
    assert out["ok"] is False


def test_reaction_force_uses_geometry_selection_and_nothing_is_left_on_failure(geo):
    fake, s = geo
    out = J(s.add_reaction_force("Base"))
    assert out["ok"] is True and out["x"] is None and out["solved"] is False
    probe = fake.static.Solution.Children[0]
    assert (
        "GeometrySelection" in str(probe.LocationMethod) and probe.GeometryLocation.Name == "Base"
    )
    assert "Location =" not in fake.scripts[-1].replace("GeometryLocation", "").replace(
        "_obj.LocationMethod", ""
    )

    def hook(o):
        o._fail["GeometryLocation"] = RuntimeError("no scope")

    fake.result_hooks = [hook]
    n = len(fake.static.Solution.Children)
    out = J(s.add_reaction_force("Base"))
    assert out["ok"] is False and len(fake.static.Solution.Children) == n
    assert (
        J(s.add_reaction_force("Empty"))["ok"] is False and len(fake.static.Solution.Children) == n
    )


# -- A4 results on an unsolved model ---------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.add_equivalent_stress(),
        lambda s: s.add_total_deformation(),
        lambda s: s.add_principal_stress("max"),
        lambda s: s.add_directional_deformation("Z"),
        lambda s: s.add_normal_stress("x"),
        lambda s: s.add_stress_intensity(),
        lambda s: s.add_stress_tool(),
    ],
)
def test_unsolved_results_have_null_values_and_a_note(mech, call):
    fake, s = mech
    out = J(call(s))
    assert out["ok"] is True and out["created"] is True and out["solved"] is False
    assert out.get("max") is None and out.get("min") is None
    assert "not solved yet" in out["note"]
    assert not getattr(fake.static.Solution, "evaluated", False)


def test_solved_results_report_values(mech):
    fake, s = mech
    fake.static.Solution.Status = "Done"
    out = J(s.add_equivalent_stress())
    assert (
        out["solved"] is True
        and out["max"] == "12.5 [Pa]"
        and out["min"] == "-3 [Pa]"
        and out["note"] is None
    )
    assert fake.static.Solution.evaluated is True


def test_failed_evaluation_removes_the_result(mech):
    fake, s = mech
    fake.static.Solution.Status = "Done"

    def boom():
        raise RuntimeError("evaluation failed")

    fake.static.Solution.EvaluateAllResults = boom
    out = J(s.add_equivalent_stress())
    assert out["ok"] is False and fake.static.Solution.Children == []


def test_modal_frequencies_unsolved_is_an_error(mech):
    fake, s = mech
    out = J(s.get_modal_frequencies(1))
    assert out["ok"] is False and "not solved" in out["error"]
    assert fake.modal.Solution.Children == []


def test_modal_frequencies_on_static_is_an_error(mech):
    fake, s = mech
    fake.static.Solution.Status = "Done"
    out = J(s.get_modal_frequencies(0))
    assert out["ok"] is False and "not a Modal" in out["error"]
    assert fake.static.Solution.Children == []


def test_modal_frequencies_solved(mech):
    fake, s = mech
    fake.modal.Solution.Status = "Done"
    state = {"i": 0}

    def hook(o):
        state["i"] += 1
        o.ReportedFrequency = Q(100.0 * state["i"], "Hz")
        o.Mode = 0

    fake.result_hooks = [hook]
    # AddTotalDeformation sets .Mode afterwards, so make the hook-created frequency stick per mode
    out = J(s.get_modal_frequencies(1))
    assert out["ok"] is True and out["results_created"] is True
    assert [f["mode"] for f in out["frequencies"]] == [1, 2, 3, 4, 5, 6]
    assert out["frequencies"][0]["frequency_hz"] == 100.0


def test_modal_frequencies_failure_removes_what_it_added(mech):
    fake, s = mech
    fake.modal.Solution.Status = "Done"

    def boom():
        raise RuntimeError("evaluation failed")

    fake.modal.Solution.EvaluateAllResults = boom
    out = J(s.get_modal_frequencies(1))
    assert out["ok"] is False and out["rolled_back"] is True
    assert fake.modal.Solution.Children == []


def test_all_modes_unsolved_or_static_creates_nothing(mech):
    fake, s = mech
    assert J(s.add_total_deformation_all_modes(3, 1))["ok"] is False
    fake.static.Solution.Status = "Done"
    assert J(s.add_total_deformation_all_modes(3, 0))["ok"] is False
    fake.modal.Solution.Status = "Done"
    out = J(s.add_total_deformation_all_modes(99, 1))
    assert out["ok"] is False and "exceeds" in out["error"]
    assert fake.modal.Solution.Children == [] and fake.static.Solution.Children == []
    out = J(s.add_total_deformation_all_modes(3, 1))
    assert out["ok"] is True and out["modes_added"] == 3 and len(fake.modal.Solution.Children) == 3


def test_total_deformation_mode_checks_happen_before_creating(mech):
    fake, s = mech
    assert J(s.add_total_deformation(2, 0))["ok"] is False  # static
    assert J(s.add_total_deformation(99, 1))["ok"] is False  # beyond the modes
    assert fake.static.Solution.Children == [] and fake.modal.Solution.Children == []
    assert J(s.add_total_deformation(2, 1))["ok"] is True


# -- A6 silent no-ops -------------------------------------------------------------------------------


def test_missing_named_selection_in_results_is_an_error_and_creates_nothing(geo):
    fake, s = geo
    for call in (
        lambda: s.add_equivalent_stress("doesnotexist"),
        lambda: s.add_directional_deformation("X", "doesnotexist"),
        lambda: s.add_equivalent_stress("Empty"),
    ):
        out = J(call())
        assert out["ok"] is False and out["created"] is False
    assert fake.static.Solution.Children == []


def test_scoped_result_gets_its_location(geo):
    fake, s = geo
    assert J(s.add_equivalent_stress("Base"))["ok"] is True
    assert fake.static.Solution.Children[0].Location.Name == "Base"


def test_principal_stress_label_and_method_agree(mech):
    fake, s = mech
    for which, label in (("max", "Maximum"), ("MID", "Middle"), ("min", "Minimum")):
        out = J(s.add_principal_stress(which))
        assert out["ok"] is True and out["type"] == label + " Principal Stress"


def test_directional_deformation_axis(mech):
    fake, s = mech
    out = J(s.add_directional_deformation("z"))
    assert out["axis"] == "Z" and "ZAxis" in fake.scripts[-1]


# -- A7 numeric read-back -------------------------------------------------------------------------


def test_mesh_size_is_read_back(mech):
    fake, s = mech
    out = J(s.set_mesh_element_size(5.0))
    assert out["ok"] is True and out["element_size_mm"] == pytest.approx(5.0)


def test_mesh_size_that_mechanical_ignores_is_an_error(mech):
    fake, s = mech

    class Stubborn:
        pass

    # a mesh that silently keeps its old size, like Mechanical does for an out-of-range value
    mesh = fake.model.Mesh
    old = mesh.ElementSize
    mesh._fail["ElementSize"] = None
    object.__setattr__(mesh, "_fail", {})
    type(mesh).ElementSize = property(lambda self: old, lambda self, v: None)
    try:
        out = J(s.set_mesh_element_size(5.0))
    finally:
        del type(mesh).ElementSize
    assert out["ok"] is False and "did not accept" in out["error"]


def test_2d_behavior_on_a_3d_model_is_an_error(mech):
    fake, s = mech
    out = J(s.set_2d_behavior("plane_strain"))
    assert out["ok"] is False and "no 2D geometry" in out["error"]
    assert fake.model.Geometry.Model2DBehavior == "PlaneStress"
    fake.model.Geometry.IsPropertyVisible = lambda n: True
    out = J(s.set_2d_behavior("plane_strain"))
    assert out["ok"] is True and out["behavior"] == "PlaneStrain"


# -- A8 nonlinear -------------------------------------------------------------------------------------


def test_nonlinear_changes_only_what_is_passed(mech):
    fake, s = mech
    st = fake.static.AnalysisSettings
    out = J(s.set_nonlinear_settings(0, large_deflection=True))
    assert out["ok"] is True and out["applied"] == ["LargeDeflection"]
    assert st.LargeDeflection is True
    assert (
        st.AutomaticTimeStepping == "ProgramControlled"
        and st.InitialSubsteps == 1
        and st.MaximumSubsteps == 10
    )


def test_nonlinear_nothing_to_change_is_an_error(mech):
    fake, s = mech
    assert J(s.set_nonlinear_settings(0))["ok"] is False


def test_nonlinear_failure_restores_every_setting(mech):
    fake, s = mech
    st = fake.static.AnalysisSettings
    st._fail["MaximumSubsteps"] = RuntimeError("rejected")
    out = J(
        s.set_nonlinear_settings(
            0, large_deflection=True, initial_substeps=5, min_substeps=2, max_substeps=50
        )
    )
    assert out["ok"] is False and out["restored"] is True
    assert st.LargeDeflection is False and st.InitialSubsteps == 1 and st.MinimumSubsteps == 1


# -- B2/B4 suppress ----------------------------------------------------------------------------------


def test_suppress_matching_nothing_is_an_error(geo):
    fake, s = geo
    out = J(s.suppress_bodies("zzz"))
    assert out["ok"] is False and out["affected"] == 0


def test_suppress_by_prefix_and_all(geo):
    fake, s = geo
    out = J(s.suppress_bodies("Pl"))
    assert (
        out["ok"] is True
        and out["affected"] == 2
        and all(b.Suppressed for b in fake.model.Geometry.Bodies)
    )
    out = J(s.suppress_bodies("", suppress=False, all_bodies=True))
    assert out["affected"] == 2 and not any(b.Suppressed for b in fake.model.Geometry.Bodies)


# -- B5 first-match-only -----------------------------------------------------------------------------


def test_assign_material_changes_every_body_of_that_name(geo):
    fake, s = geo
    out = J(s.assign_material("Plate", "Q235"))
    assert out["ok"] is True and out["bodies_changed"] == 2
    assert {b.Material for b in fake.model.Geometry.Bodies} == {"Q235"}


def test_assign_material_validates_the_material(geo):
    fake, s = geo
    out = J(s.assign_material("Plate", "Unobtainium"))
    assert out["ok"] is False and "Q235" in out["available"]
    assert {b.Material for b in fake.model.Geometry.Bodies} == {"Structural Steel"}
    assert J(s.assign_material("Nobody", "Q235"))["ok"] is False


def test_assign_material_restores_on_failure(geo):
    fake, s = geo
    second = fake.model.Geometry.Bodies[1]
    second._fail["Material"] = RuntimeError("COM error")
    out = J(s.assign_material("Plate", "Q235"))
    assert out["ok"] is False and out["restored"] is True
    assert fake.model.Geometry.Bodies[0].Material == "Structural Steel"


def test_body_sizing_covers_all_same_named_bodies_and_is_reused(geo):
    fake, s = geo
    out = J(s.set_body_mesh_size("Plate", 2.0))
    assert out["ok"] is True and out["bodies"] == 2 and out["created"] is True
    sz = fake.model.Mesh.Sizings
    assert len(sz) == 1 and sorted(sz[0].Location.Ids) == [101, 102]
    out = J(s.set_body_mesh_size("Plate", 3.0))
    assert out["reused"] is True and out["created"] is False and len(fake.model.Mesh.Sizings) == 1
    assert sz[0].ElementSize.Value == pytest.approx(0.003)
    assert J(s.set_body_mesh_size("Nobody", 3.0))["ok"] is False
    assert len(fake.model.Mesh.Sizings) == 1


def _regions(fake, *types):
    return [fake.model.Connections.AddContactRegion() for _ in types], types


def test_set_contact_type_filters_and_reports_previous_types(mech):
    fake, s = mech
    regs = [fake.model.Connections.AddContactRegion() for _ in range(3)]
    regs[1].ContactType = "Rough"
    # the fake compares ContactType by name, the namespace stringifies to the member name
    out = J(s.set_contact_type("frictionless", region_names="Contact Region 3"))
    assert out["ok"] is True and out["regions_changed"] == 1 and out["regions_skipped"] == 2
    assert [(c["name"], c["was"]) for c in out["changed"]] == [("Contact Region 3", "Bonded")]
    assert str(regs[0].ContactType) == "Bonded" and str(regs[1].ContactType) == "Rough"
    out = J(s.set_contact_type("bonded", only_if_currently="rough"))
    assert out["regions_changed"] == 1 and out["changed"][0]["name"] == "Contact Region 2"
    assert J(s.set_contact_type("bonded", region_names="nope"))["ok"] is False
    assert "overwrites EVERY" in s.set_contact_type.__doc__ or "overwrites EVERY" in (
        s.set_contact_type.__wrapped__.__doc__
    )


def test_set_contact_type_with_no_regions(mech):
    fake, s = mech
    assert J(s.set_contact_type("bonded"))["ok"] is False


def test_add_contact_rolls_back(geo):
    fake, s = geo
    out = J(s.add_contact("bonded", source_entity_ids="11", target_entity_ids="21"))
    assert out["ok"] is True and len(fake.model.ContactRegions) == 1
    fake.model.ContactRegions.clear()
    orig = fake.model.Connections.AddContactRegion

    def broken():
        r = orig()
        r._fail["TargetLocation"] = RuntimeError("bad target")
        return r

    fake.model.Connections.AddContactRegion = broken
    out = J(s.add_contact("bonded", source_entity_ids="11", target_entity_ids="21"))
    assert out["ok"] is False and fake.model.ContactRegions == []
    out = J(s.add_contact("bonded", source_entity_ids="11", target_entity_ids="777"))
    assert out["ok"] is False and "target" in out["error"]


# -- B1 no reliance on leaked globals ----------------------------------------------------------------

import ast  # noqa: E402
import builtins  # noqa: E402

#: Names Mechanical provides to every script (its persistent globals) -- nothing else may be read
#: without being defined by the script itself.
MECH_GLOBALS = {
    "ExtAPI",
    "DataModelObjectCategory",
    "SelectionTypeEnum",
    "Quantity",
    "LoadDefineBy",
    "ContactType",
    "Ansys",
}


def undefined_names(script):
    tree = ast.parse(script)
    loaded, stored = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (stored if isinstance(n.ctx, (ast.Store, ast.Del)) else loaded).add(n.id)
        elif isinstance(n, ast.FunctionDef):
            stored.add(n.name)
            stored.update(a.arg for a in n.args.args)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            stored.add(n.name)
        elif isinstance(n, ast.Lambda):
            stored.update(a.arg for a in n.args.args)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            stored.update((a.asname or a.name).split(".")[0] for a in n.names)
    return loaded - stored - MECH_GLOBALS - set(dir(builtins))


def test_no_generated_script_reads_a_name_it_does_not_define(geo):
    fake, s = geo
    calls = [
        lambda: s.add_fixed_support("Base"),
        lambda: s.add_fixed_support("", box_mm="0,1,0,1,0,1"),
        lambda: s.add_fixed_support("", entity_ids="11"),
        lambda: s.add_force("Base", 1, 2, 3),
        lambda: s.add_pressure("Base", 5.0),
        lambda: s.add_displacement("Base", 1.0),
        lambda: s.add_remote_displacement("Base", 1.0, rot_x_deg=2.0),
        lambda: s.add_remote_force("Base", 1.0),
        lambda: s.add_moment("Base", 1.0),
        lambda: s.add_standard_gravity(),
        lambda: s.add_reaction_force("Base"),
        lambda: s.add_equivalent_stress("Base"),
        lambda: s.add_directional_deformation("X"),
        lambda: s.add_principal_stress("max"),
        lambda: s.add_stress_tool(),
        lambda: s.add_total_deformation(0),
        lambda: s.add_total_deformation(2, 1),
        lambda: s.add_total_deformation_all_modes(2, 1),
        lambda: s.get_modal_frequencies(1),
        lambda: s.add_normal_stress("y"),
        lambda: s.add_stress_intensity(),
        lambda: s.set_mesh_element_size(3.0),
        lambda: s.set_body_mesh_size("Plate", 2.0),
        lambda: s.set_2d_behavior("axisymmetric"),
        lambda: s.set_nonlinear_settings(0, True, True, 5, 2, 20, 3),
        lambda: s.set_contact_type("frictional", 0.2, "a,b", "bonded"),
        lambda: s.add_contact("bonded", source_entity_ids="11", target_box_mm="0,1,,,,"),
        lambda: s.assign_material("Plate", "Q235"),
        lambda: s.suppress_bodies("P"),
        lambda: s.delete_named_selection("Base"),
        lambda: s.convert_prefix_to_point_mass("P"),
        lambda: s.convert_part_to_point_mass("PartA"),
        lambda: s.solve_analysis(0),
        lambda: s.get_solve_status(0),
    ]
    for c in calls:
        c()
    export = ms.export_nodal_script("Normal Stress", "C:/x/out.json", 0)
    assert len(fake.scripts) >= len(calls) - 3
    for sc in fake.scripts + [export]:
        assert not undefined_names(sc), (undefined_names(sc), sc[:200])


def test_a_box_script_leaves_nothing_the_export_needs(geo):
    """The review's B1: export used _K, left behind by an earlier box_mm call."""
    fake, s = geo
    scope = fake.env()
    fake.run(ms.location_snippet(box_mm="0,1000,,,,"), shared=scope)
    assert "_K" in scope  # the old failure mode: a leaked global
    export = ms.export_nodal_script("Normal Stress", "C:/x/out.json", 0)
    assert "_K" not in undefined_names(export)
    out = fake.run(export)  # fresh namespace: must not die with NameError
    assert "NameError" not in out and "_K" not in out
