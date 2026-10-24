"""Argument validation and hostile strings, across every tool."""

import ast
import json

import pytest

import mechanical_scripts as ms

HOSTILE = [
    'quo"te',
    "back\\slash",
    "trail\\",
    "new\nline",
    "cr\rlf",
    "tab\tchar",
    "'single'",
    '"; import os; os.system("x"); "',
    '\\"\\n',
    "café_Øµ°",
    "世界",
    "emoji \U0001f600",
    "percent %s %d {0} {x}",
]
REJECTED = ["nul\x00char", "bell\x07", "line sep", "para sep", "del\x7f", "x" * (ms.MAX_STR + 1)]


def J(s):
    return json.loads(s)


def _norm(v):
    """IronPython strings are UTF-16: an astral character written as a surrogate pair is one
    character there. CPython keeps the two surrogates apart, so recombine them to compare."""
    return v.encode("utf-16", "surrogatepass").decode("utf-16")


# -- the escaping layer ------------------------------------------------------------------------


@pytest.mark.parametrize("text", HOSTILE)
def test_lit_roundtrips_through_the_python_parser(text):
    src = ms.lit(text)
    assert src.isascii()
    assert _norm(ast.literal_eval(src)) == text


@pytest.mark.parametrize("text", REJECTED)
def test_esc_refuses_control_characters_and_huge_strings(text):
    with pytest.raises(ValueError):
        ms.esc(text)


def test_newline_cannot_close_the_literal():
    # a raw newline in the output would end the string literal and run the rest as code
    assert "\n" not in ms.esc("a\nb") and "\r" not in ms.esc("a\rb")
    compile("x = " + ms.lit("a\nb\rc") + "\n", "s", "exec")


# -- numbers -----------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "1", None, 1e30])
def test_num_refuses(bad):
    with pytest.raises(ValueError):
        ms.num("v", bad)


def test_num_ranges():
    assert ms.num("v", 3) == 3.0
    with pytest.raises(ValueError):
        ms.num("v", 0, positive=True)
    with pytest.raises(ValueError):
        ms.num("v", -1, lo=0)
    with pytest.raises(ValueError):
        ms.num("v", 5, hi=4)


def test_integer_and_index():
    assert ms.index(0) == 0
    for bad in (-1, 1000, True, 1.5, "0"):
        with pytest.raises(ValueError):
            ms.index(bad)


@pytest.mark.parametrize(
    "text", ["1_0", "١٠", "0", "-3", "a", "1.5", "99999999999", " ", "1e3", "²"]
)
def test_parse_ids_refuses(text):
    with pytest.raises(ValueError):
        ms.parse_ids(text)


def test_parse_ids_accepts_plain_lists_and_dedupes():
    assert ms.parse_ids("12, 15 18,12") == [12, 15, 18]


@pytest.mark.parametrize(
    "box",
    [
        "nan,1,0,1,0,1",
        "inf,2,0,1,0,1",
        "0,1e400,0,1,0,1",
        "1_0,20,0,1,0,1",
        "١,2,0,1,0,1",
        "0,1e12,0,1,0,1",
        "x,1,0,1,0,1",
    ],
)
def test_box_refuses_non_finite_and_odd_numbers(box):
    with pytest.raises(ValueError):
        ms.parse_box_mm(box)


def test_box_blank_sides_stay_open():
    assert ms.parse_box_mm("0,10,,none,-1,1") == [0.0, 10.0, None, None, -1.0, 1.0]


# -- every tool, hostile strings ---------------------------------------------------------------

STRING_TOOLS = [
    ("assign_material", {"body_name": "H", "material_name": "Q235"}, "body_name"),
    ("assign_material", {"body_name": "b", "material_name": "H"}, "material_name"),
    ("delete_named_selection", {"name": "H"}, "name"),
    ("suppress_bodies", {"name_prefix": "H"}, "name_prefix"),
    ("set_body_mesh_size", {"body_name": "H", "element_size_mm": 2.0}, "body_name"),
    ("add_fixed_support", {"named_selection": "H"}, "named_selection"),
    ("add_frictionless_support", {"named_selection": "H"}, "named_selection"),
    ("add_force", {"named_selection": "H", "fx_n": 1.0}, "named_selection"),
    ("add_pressure", {"named_selection": "H", "magnitude_pa": 1.0}, "named_selection"),
    ("add_displacement", {"named_selection": "H", "x_mm": 1.0}, "named_selection"),
    ("add_remote_displacement", {"named_selection": "H", "x_mm": 1.0}, "named_selection"),
    ("add_remote_force", {"named_selection": "H", "fx_n": 1.0}, "named_selection"),
    ("add_moment", {"named_selection": "H", "mx_nm": 1.0}, "named_selection"),
    ("add_equivalent_stress", {"named_selection": "H"}, "named_selection"),
    ("add_directional_deformation", {"axis": "X", "named_selection": "H"}, "named_selection"),
    ("add_reaction_force", {"named_selection": "H"}, "named_selection"),
    ("convert_prefix_to_point_mass", {"body_name_prefix": "H"}, "body_name_prefix"),
    ("convert_part_to_point_mass", {"part_name": "H"}, "part_name"),
    ("set_contact_type", {"contact_type": "bonded", "region_names": "H"}, "region_names"),
    (
        "add_fixed_support",
        {"named_selection": "", "box_mm": "0,1,0,1,0,1", "body_name": "H"},
        "body_name",
    ),
]


def _literals(script):
    """Every string literal value in the script."""
    out = set()
    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(_norm(node.value))
    return out


@pytest.mark.parametrize("hostile", HOSTILE)
@pytest.mark.parametrize("tool,kwargs,field", STRING_TOOLS)
def test_hostile_text_arrives_intact_and_compiles(mech, tool, kwargs, field, hostile):
    fake, s = mech
    kw = {k: (hostile if v == "H" else v) for k, v in kwargs.items()}
    kw[field] = hostile if kwargs[field] == "H" else kw[field]
    getattr(s, tool)(**kw)
    assert fake.scripts, "no script was generated"
    for sc in fake.scripts:
        compile(sc, "<generated>", "exec")
        assert sc.isascii(), "generated script must be pure ASCII"
    lits = set().union(*[_literals(sc) for sc in fake.scripts])
    expected = hostile
    # the convert_* tools use a sanitised label only for the named selection name
    assert expected in lits


@pytest.mark.parametrize("bad", REJECTED)
@pytest.mark.parametrize("tool,kwargs,field", STRING_TOOLS)
def test_control_characters_are_refused_before_anything_runs(mech, tool, kwargs, field, bad):
    fake, s = mech
    kw = {k: (bad if v == "H" else v) for k, v in kwargs.items()}
    out = J(getattr(s, tool)(**kw))
    assert out["ok"] is False
    assert fake.scripts == []


def test_the_review_example_cafe_is_not_a_script_error(mech):
    fake, s = mech
    out = J(s.delete_named_selection("café"))
    assert out["ok"] is False and "café" in out["error"]


# -- numeric validation, tool by tool ----------------------------------------------------------

BAD_NUMBERS = [float("nan"), float("inf"), -float("inf")]


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_non_finite_numbers_are_refused_everywhere(mech, bad):
    fake, s = mech
    ns = "plain"
    calls = [
        lambda: s.add_force(ns, fx_n=bad),
        lambda: s.add_force(ns, fz_n=bad),
        lambda: s.add_pressure(ns, bad),
        lambda: s.add_displacement(ns, x_mm=bad),
        lambda: s.add_remote_displacement(ns, rot_x_deg=bad),
        lambda: s.add_remote_force(ns, fy_n=bad),
        lambda: s.add_moment(ns, mz_nm=bad),
        lambda: s.set_mesh_element_size(bad),
        lambda: s.set_body_mesh_size("b", bad),
        lambda: s.convert_prefix_to_point_mass("p", bad),
        lambda: s.convert_part_to_point_mass("p", bad),
        lambda: s.set_contact_type("frictional", bad),
        lambda: s.add_contact(
            "bonded", source_entity_ids="1", target_entity_ids="2", friction_coefficient=bad
        ),
        lambda: s.add_standard_gravity(0, bad, 0, 0),
    ]
    for c in calls:
        assert J(c())["ok"] is False
    assert fake.scripts == []


def test_box_mm_with_nan_inf_overflow_never_reaches_a_script(mech):
    fake, s = mech
    for box in ("nan,1,0,1,0,1", "0,inf,0,1,0,1", "0,1e400,0,1,0,1"):
        assert J(s.add_fixed_support("", box_mm=box))["ok"] is False
    assert fake.scripts == []


@pytest.mark.parametrize("size", [-5, 0, 1e-6, 1e9, -0.0])
def test_mesh_size_range(mech, size):
    fake, s = mech
    assert J(s.set_mesh_element_size(size))["ok"] is False
    assert fake.scripts == []


@pytest.mark.parametrize("p", [0, -1, 0.05, float("nan"), 1000])
def test_proximity_multiplier_range(mech, p):
    fake, s = mech
    assert J(s.convert_prefix_to_point_mass("x", p))["ok"] is False
    assert fake.scripts == []


@pytest.mark.parametrize("idx", [-1, 1000, True, 1.5])
def test_analysis_index_is_validated(mech, idx):
    fake, s = mech
    assert J(s.add_equivalent_stress("", idx))["ok"] is False
    assert J(s.solve_analysis(idx))["ok"] is False
    assert J(s.get_solve_status(idx))["ok"] is False
    assert J(s.list_boundary_conditions(idx))["ok"] is False
    assert fake.scripts == []


def test_friction_and_substeps_ranges(mech):
    fake, s = mech
    assert J(s.set_contact_type("frictional", 0.0))["ok"] is False
    assert J(s.set_contact_type("bonded", -0.1))["ok"] is False
    assert J(s.set_contact_type("bonded", 50))["ok"] is False
    assert J(s.set_nonlinear_settings(0, initial_substeps=0))["ok"] is False
    assert J(s.set_nonlinear_settings(0, number_of_steps=-1))["ok"] is False
    assert J(s.set_nonlinear_settings(0, min_substeps=10, max_substeps=5))["ok"] is False
    assert fake.scripts == []


def test_enumerated_arguments_are_checked(mech):
    fake, s = mech
    assert J(s.add_directional_deformation("Q"))["ok"] is False
    assert J(s.add_principal_stress("foo"))["ok"] is False
    assert J(s.add_normal_stress("w"))["ok"] is False
    assert J(s.set_2d_behavior("shell"))["ok"] is False
    assert J(s.set_contact_type("glued"))["ok"] is False
    assert J(s.add_fixed_support("a", entity_ids="1"))["ok"] is False  # both locators
    assert J(s.add_fixed_support("", entity_type="solid", entity_ids="1"))["ok"] is False
    assert fake.scripts == []


def test_displacement_needs_at_least_one_dof(mech):
    fake, s = mech
    assert J(s.add_displacement("ns"))["ok"] is False
    assert J(s.add_remote_displacement("ns"))["ok"] is False


def test_gravity_direction_normalisation():
    assert ms.gravity_direction(0, -9.81, 0)[0] == "NegativeYAxis"
    assert ms.gravity_direction(3, 0, 0)[0] == "PositiveXAxis"
    for v in ((0, 0, 0), (1, 1, 0), (0.5, 0.5, 0.5)):
        with pytest.raises(ValueError):
            ms.gravity_direction(*v)
    with pytest.raises(ValueError):
        ms.gravity_direction(float("nan"), 0, 0)


def test_suppress_needs_a_prefix_or_the_explicit_flag(mech):
    fake, s = mech
    assert J(s.suppress_bodies())["ok"] is False
    assert J(s.suppress_bodies("x", all_bodies=True))["ok"] is False
    assert fake.scripts == []
    s.suppress_bodies("", all_bodies=True)
    assert fake.scripts  # runs only when asked for on purpose


def test_convert_prefix_empty_is_refused(mech):
    fake, s = mech
    assert J(s.convert_prefix_to_point_mass(""))["ok"] is False
    assert J(s.convert_prefix_to_point_mass("   "))["ok"] is False
    assert J(s.convert_part_to_point_mass(""))["ok"] is False
    assert fake.scripts == []
