"""Fourth audit round."""

import json

import pytest

import mechanical_scripts as ms


def J(s):
    return json.loads(s)


@pytest.fixture
def geo(mech):
    fake, s = mech
    b1 = fake.add_body("Plate", part="PartA")
    fake.add_face(b1, 11, (0.1, 0, 0))
    fake.add_face(b1, 12, (0.3, 0, 0))
    fake.build_geo()
    fake.add_ns("Faces", [11, 12])
    return fake, s, b1


def _loads(fake):
    return [o for o in fake.model.Analyses[0].Children if getattr(o, "Name", "") != "Solution"]


# -- body-type scope --------------------------------------------------------------------------


def test_a_load_scoped_to_a_body_named_selection_is_refused_and_rolled_back(geo):
    fake, s, b1 = geo
    fake.add_ns("BodyNS", [b1._geo.Id])
    before = len(fake.model.Analyses[0].Children)
    out = J(s.add_force("BodyNS", 0, 0, -10, analysis_index=0))
    assert out["ok"] is False
    assert len(fake.model.Analyses[0].Children) == before  # nothing left under the analysis


def test_a_face_scope_load_is_fully_defined(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, -10, analysis_index=0))
    assert out["ok"] is True and out["analysis"]["name"]


# -- analysis selection ---------------------------------------------------------------------


def test_a_lone_surrogate_in_a_name_is_refused_before_anything_runs(geo):
    fake, s, _ = geo
    n = len(fake.scripts)
    out = J(s.add_force("Faces" + chr(0xD800), 0, 0, -1, analysis_index=0))
    assert out["ok"] is False and "surrogate" in out["error"]
    assert len(fake.scripts) == n


def test_several_analyses_need_an_explicit_choice(geo, monkeypatch):
    fake, s, _ = geo
    monkeypatch.setenv("MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS", "1")
    out = J(s.add_force("Faces", 0, 0, -1))
    assert out["ok"] is False and "analysis_index" in out["error"]
    assert not _loads(fake)


def test_expected_type_mismatch_is_refused_and_leaves_nothing(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, -1, analysis_index=0, expect_analysis_type="Modal"))
    assert out["ok"] is False
    assert not [o for o in _loads(fake) if "Force" in str(o.Name)]


def test_analysis_can_be_chosen_by_name_and_is_reported(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, -1, analysis_name="Static Structural"))
    assert out["ok"] is True and out["analysis"]["name"] == "Static Structural"
    assert out["analysis"]["index"] == 0


def test_an_unknown_analysis_name_is_an_error(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, -1, analysis_name="No Such Analysis"))
    assert out["ok"] is False


def test_the_name_wins_over_a_conflicting_index(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, -1, analysis_index=1, analysis_name="Static Structural"))
    assert out["ok"] is False or out["analysis"]["index"] == 0


# -- point mass: scope loss and multi-body numerics -----------------------------------------------

from fake_mech import Q  # noqa: E402
from test_pointmass import scene  # noqa: E402


def _two_bodies(fake):
    eq, plate = scene(fake)
    eq2 = fake.add_body("EQ_other", part="Equip", mass=0.002)
    fake.add_face(eq2, 30, (0.0, 0.0, 0.02), normal=(0, 0, 1))
    eq2.CentroidX, eq2.CentroidZ = Q(0.05, "m"), Q(0.03, "m")
    fake.build_geo()
    return eq, eq2, plate


def test_multi_body_point_mass_mass_cg_and_parallel_axis_inertia(mech):
    """m1=0.008 at (0,0,10 mm), m2=0.002 at (50,0,30 mm), each with Ip = 0.01 kg m^2.
    CG = (10, 0, 14) mm; I = sum(Ip + m * d^2) about the combined CG."""
    fake, s = mech
    _two_bodies(fake)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True and out["cg_mm"] == [10.0, 0.0, 14.0]
    pm = fake.pm_created
    assert pm.Mass.Value == pytest.approx(0.01)
    # Ixx: d = (-10,0,-4) mm and (40,0,16) mm
    assert pm.MassMomentOfInertiaX.Value == pytest.approx(
        0.02 + 0.008 * 0.004**2 + 0.002 * 0.016**2, rel=1e-9
    )
    assert pm.MassMomentOfInertiaY.Value == pytest.approx(
        0.02 + 0.008 * (0.01**2 + 0.004**2) + 0.002 * (0.04**2 + 0.016**2), rel=1e-9
    )
    assert pm.MassMomentOfInertiaZ.Value == pytest.approx(
        0.02 + 0.008 * 0.01**2 + 0.002 * 0.04**2, rel=1e-9
    )


def test_a_load_scoped_to_the_converted_bodies_blocks_the_conversion(mech):
    fake, s = mech
    eq, plate = scene(fake)
    fake.add_ns("EqFace", [10])
    assert J(s.add_force("EqFace", 0, 0, -5, analysis_index=0))["ok"] is True
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False and out["scope_loss"] and "acknowledge_scope_loss" in out["error"]
    assert eq.Suppressed is False and not fake.model.Geometry.PointMasses
    out = J(s.convert_prefix_to_point_mass("EQ_", acknowledge_scope_loss=True))
    assert out["ok"] is True and out["scope_loss"] and eq.Suppressed is True


# -- zero loads, suppressed contacts, named-selection ids --------------------------------------------


def test_an_all_zero_force_needs_allow_zero(geo):
    fake, s, _ = geo
    out = J(s.add_force("Faces", 0, 0, 0, analysis_index=0))
    assert out["ok"] is False and "allow_zero" in out["error"]
    assert J(s.add_force("Faces", 0, 0, 0, allow_zero=True, analysis_index=0))["ok"] is True


def test_set_contact_type_skips_suppressed_regions_unless_asked(geo):
    fake, s, _ = geo
    b2 = fake.add_body("Block", part="PartB")
    fake.add_face(b2, 21, (0.5, 0, 0))
    fake.build_geo()
    assert J(s.add_contact("bonded", "11", "", "", "21", "", "", "face", 0.0))["ok"] is True
    cr = fake.model.ContactRegions[0]
    cr.Suppressed = True
    cr.ContactType = "Bonded"
    out = J(s.set_contact_type("Frictionless"))
    assert cr.ContactType == "Bonded", out
    out = J(s.set_contact_type("Frictionless", include_suppressed=True))
    assert out["ok"] is True and str(cr.ContactType).endswith("Frictionless")


def test_named_selections_list_ids_and_delete_by_id_handles_duplicate_names(geo):
    fake, s, _ = geo
    fake.add_ns("Dup", [11])
    fake.add_ns("Dup", [12])
    listed = J(s.list_named_selections())
    ids = [n["id"] for n in listed["named_selections"] if n["name"] == "Dup"]
    assert len(ids) == 2 and ids[0] != ids[1]
    out = J(s.delete_named_selection(object_id=ids[0]))
    assert out["ok"] is True
    left = [n for n in J(s.list_named_selections())["named_selections"] if n["name"] == "Dup"]
    assert [n["id"] for n in left] == [ids[1]]


# -- export: load step ------------------------------------------------------------------------------

from test_runtime import _solved_export_fixture  # noqa: E402


def test_export_of_a_multi_step_analysis_requires_an_explicit_load_step(mech, tmp_path):
    fake, s = mech
    _solved_export_fixture(fake)
    fake.static.AnalysisSettings.NumberOfSteps = 3
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0))
    assert out["ok"] is False and "load_step" in out["error"]
    assert not (tmp_path / "n.json").exists()
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0, load_step=2))
    assert out["ok"] is True
    doc = json.loads((tmp_path / "n.json").read_text())
    assert doc["step"]["number_of_steps"] == 3


def test_export_load_step_is_validated(mech, tmp_path):
    fake, s = mech
    _solved_export_fixture(fake)
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "n.json"), 0, load_step=-1))
    assert out["ok"] is False


# -- modal: partial answers are flagged -------------------------------------------------------------


def test_modal_frequencies_report_a_partial_read_when_results_are_not_added(mech):
    fake, s = mech
    fake.modal.Solution.Status = "Done"

    def hook(o):
        o.ReportedFrequency = Q(50.0, "Hz")

    fake.result_hooks = [hook]
    assert J(s.add_total_deformation(1, 1))["created"] is True
    out = J(s.get_modal_frequencies(1, add_results_if_missing=False))
    assert out["ok"] is True and out["partial"] is True
    assert out["missing_modes"] == [2, 3, 4, 5, 6] and "warning" in out
    assert [f["mode"] for f in out["frequencies"]] == [1]


# -- linearize ------------------------------------------------------------------------------------

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
from test_postproc import grid2d, write  # noqa: E402

import mechanical_postproc as pp  # noqa: E402


def test_a_section_line_shorter_than_an_element_is_refused(tmp_path):
    f = write(tmp_path / "n.json", *grid2d())
    with pytest.raises(ValueError, match="too short"):
        pp.linearize_file(f, (5.0, 5.0), (5.0, 5.2), "xy")


def test_a_cancelled_linearization_stops(tmp_path):
    import threading

    f = write(tmp_path / "n.json", *grid2d())
    ev = threading.Event()
    ev.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        pp.linearize_file(f, (5, 0), (5, 20), "xy", cancel=ev)


def test_a_sudden_change_of_element_size_is_not_a_gap(tmp_path):
    """Fine nodes (pitch 1) for x < 6, coarse (pitch 4) beyond: a size step, not a void."""
    pts, vals = [], []
    for x in list(range(0, 6)) + [8, 12, 16, 20]:
        step = 1 if x < 6 else 4
        for y in range(0, 21, step):
            pts.append([float(x), float(y), 0.0])
            vals.append(100.0 + 5.0 * y)
    f = write(tmp_path / "n.json", pts, vals)
    r = pp.linearize_file(f, (16, 0), (16, 20), "xy")
    assert r["membrane"] == pytest.approx(150.0, rel=1e-3)


def test_the_tool_lets_only_one_linearization_run_and_the_next_is_told_to_wait(
    server, tmp_path, monkeypatch
):
    import threading

    f = write(tmp_path / "n.json", *grid2d())
    started, release = threading.Event(), threading.Event()

    def slow(*a, cancel=None, **k):
        started.set()
        release.wait(5)
        return {}

    monkeypatch.setattr(pp, "linearize_file", slow)
    first = []
    t = threading.Thread(
        target=lambda: first.append(
            server.linearize_path(nodal_file=f, start_mm="5,0", end_mm="5,20")
        )
    )
    t.start()
    assert started.wait(5)
    second = J(server.linearize_path(nodal_file=f, start_mm="5,0", end_mm="5,20"))
    release.set()
    t.join(5)
    assert second["ok"] is False and second.get("busy") is True


# -- signed allow-list and link detection -----------------------------------------------------------


@pytest.mark.parametrize(
    "type_name,signed",
    [
        ("NormalStress", True),
        ("DirectionalDeformation", True),
        ("MaximumPrincipalStress", True),
        ("MaximumShearStress", False),
        ("EquivalentStress", False),
        ("StressIntensity", False),
        ("TotalDeformation", False),
    ],
)
def test_only_known_signed_result_types_are_called_signed(mech, tmp_path, type_name, signed):
    fake, s = mech
    res = _solved_export_fixture(fake)
    res._type = type_name
    out = J(s.export_nodal_results("Normal Stress", str(tmp_path / "e.json"), 0))
    assert out["ok"] is True and out["signed"] is signed


def test_the_link_check_uses_the_reparse_point_attribute_when_system_is_available():
    class _IO:
        class File:
            @staticmethod
            def GetAttributes(p):
                if p == "link":
                    return 0x410
                if p == "plain":
                    return 0x20
                raise OSError("no such file")

    class _Sys:
        IO = _IO

    ns = {"os": __import__("os")}
    exec(ms.LINK_DEFS, ns)
    ns["System"] = _Sys
    assert ns["_is_link"]("link") is True
    assert ns["_is_link"]("plain") is False
    assert ns["_is_link"]("missing") is False  # unreadable: not a link, the open will fail


def test_the_link_check_falls_back_to_islink_without_system(tmp_path):
    ns = {"os": __import__("os")}
    exec(ms.LINK_DEFS, ns)
    ns["System"] = None
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert ns["_is_link"](str(f)) is False


# -- temp artefacts, mesh statistics, shutdown wording --------------------------------------------


def test_stale_temp_files_are_removed_and_fresh_ones_kept(server, tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(server.tempfile, "gettempdir", lambda: str(tmp_path))
    old, fresh, other = (tmp_path / n for n in ("mech_script_a.py", "mech_out_b.txt", "keep.py"))
    for f in (old, fresh, other):
        f.write_text("x")
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    assert server._cleanup_stale_temp() == 1
    assert not old.exists() and fresh.exists() and other.exists()


def test_mesh_statistics_say_when_there_is_no_mesh(mech):
    fake, s = mech
    fake.model.Mesh.Nodes = 0
    fake.model.Mesh.Elements = 0
    out = J(s.get_mesh_statistics())
    assert out["ok"] is True and out.get("mesh_generated") is False and out.get("note")
