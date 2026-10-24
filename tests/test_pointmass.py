"""Point-mass conversion against a fake model: measure first, change last, undo on failure."""

import json

import pytest
from fake_mech import Obj, Q

import mechanical_scripts as ms

NS = "NearestFace_" + ms.ns_label("EQ_")


def J(s):
    return json.loads(s)


def scene(fake, unit_scale=1.0, with_plate=True):
    """A 20 mm equipment cube standing on a plate; the cube's CG is 10 mm above the plate.

    Geometry sizes are chosen so the search radius (3 x pinball = 37 mm) matters: plate face
    103 lies inside the equipment footprint but 42 mm from the CG, so it is attached only if
    the pinball (computed from the body volume) is wrong."""
    k = unit_scale
    eq = fake.add_body("EQ_box", part="Equip", mass=0.008)
    for i, c in enumerate(
        [
            (0, 0, 0),
            (0, 0, 0.02),
            (0.01, 0, 0.01),
            (-0.01, 0, 0.01),
            (0, 0.01, 0.01),
            (0, -0.01, 0.01),
        ]
    ):
        fake.add_face(eq, 10 + i, [v * k for v in c], normal=(0, 0, 1))
    eq.CentroidZ = Q(0.01, "m")
    plate = None
    if with_plate:
        plate = fake.add_body("Plate", part="Struct", mass=50.0)
        fake.add_face(plate, 100, [0.0, 0.0, 0.0], normal=(0, 0, 1))
        fake.add_face(plate, 101, [0.025 * k, 0.0, 0.0], normal=(0, 0, 1))
        fake.add_face(plate, 102, [0.0, 0.0, 0.2 * k], normal=(0, 0, 1))  # not coplanar
        fake.add_face(plate, 103, [0.029 * k, 0.029 * k, 0.0], normal=(0, 0, 1))  # too far
    fake.build_geo()
    fake.model.Geometry.AddPointMass = lambda: _new_pm(fake)
    fake.ext.DataModel.GeoData.Unit = "m" if k == 1.0 else "mm"
    return eq, plate


def _new_pm(fake):
    pm = Obj("Point Mass", InternalObject=Obj("io"), Activate=lambda: None, Location=None)
    pm._owner = fake.model.Geometry.PointMasses
    fake.model.Geometry.PointMasses.append(pm)
    fake.pm_created = pm
    return pm


def test_conversion_creates_point_mass_and_selection_and_suppresses(mech):
    fake, s = mech
    eq, plate = scene(fake)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True, out
    assert out["bodies_matched"] == 1 and out["bodies_suppressed"] == 1
    assert out["cg_mm"] == [0.0, 0.0, 10.0]
    assert out["attachment_faces"] == 2  # coplanar faces within the search radius only
    assert eq.Suppressed is True and plate.Suppressed is False
    assert len(fake.model.Geometry.PointMasses) == 1
    ns = fake.model.NamedSelections[0]
    assert ns.Name == NS and sorted(ns.Location.Ids) == [100, 101]
    assert fake.model.Geometry.PointMasses[0].Location.Name == NS


def test_the_search_radius_comes_from_the_volume_in_cubic_metres(mech):
    """The fake body reports its volume in mm^3; reading .Value instead of converting to m^3 makes
    the pinball 1000x too large and attaches the far face as well."""
    fake, s = mech
    scene(fake)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["attachment_faces"] == 2
    assert sorted(fake.model.NamedSelections[0].Location.Ids) == [100, 101]


def test_inertia_basis_and_warnings_are_reported(mech):
    fake, s = mech
    scene(fake)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["inertia_basis"] == "principal"
    assert any("PRINCIPAL" in w for w in out["warnings"])
    assert not any("products of inertia" in w for w in out["warnings"])  # one body only


def test_combining_bodies_warns_that_products_of_inertia_are_dropped(mech):
    fake, s = mech
    scene(fake)
    eq2 = fake.add_body("EQ_other", part="Equip", mass=0.002)
    fake.add_face(eq2, 30, (0.0, 0.0, 0.02), normal=(0, 0, 1))
    eq2.CentroidZ = Q(0.01, "m")
    fake.build_geo()
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True
    assert any("products of inertia" in w for w in out["warnings"])


def test_a_tilted_planar_face_warns_that_the_principal_axes_may_not_be_global(mech):
    fake, s = mech
    eq, _ = scene(fake)
    f = Obj("tilted", Id=77, Centroid=[0.0, 0.0, 0.01], SurfaceType="Plane")
    f.NormalAtParam = lambda u, v: (0.6, 0.0, 0.8)
    eq._geo.Faces.append(f)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True and any("not aligned" in w for w in out["warnings"])


def test_part_conversion(mech):
    fake, s = mech
    eq, plate = scene(fake)
    out = J(s.convert_part_to_point_mass("Equip"))
    assert out["ok"] is True and eq.Suppressed is True


def test_geometry_in_millimetres(mech):
    fake, s = mech
    eq, plate = scene(fake, unit_scale=1000.0)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True and out["attachment_faces"] == 2


def test_no_attachment_face_changes_nothing(mech):
    fake, s = mech
    eq, plate = scene(fake, with_plate=False)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False and "nothing was changed" in out["error"]
    assert eq.Suppressed is False
    assert fake.model.Geometry.PointMasses == [] and fake.model.NamedSelections == []


def test_zero_matches_is_an_error(mech):
    fake, s = mech
    scene(fake)
    out = J(s.convert_prefix_to_point_mass("zzz"))
    assert out["ok"] is False and fake.model.Geometry.PointMasses == []
    out = J(s.convert_part_to_point_mass("NoSuchPart"))
    assert out["ok"] is False


def test_empty_prefix_never_matches_every_body(mech):
    fake, s = mech
    eq, plate = scene(fake)
    assert J(s.convert_prefix_to_point_mass(""))["ok"] is False
    assert eq.Suppressed is False and plate.Suppressed is False


def test_surface_bodies_alone_are_not_converted(mech):
    fake, s = mech
    eq, _ = scene(fake)
    eq._geo.BodyType = "GeoBodySheet"
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False and "solid" in out["error"].lower()


def test_failure_while_scoping_undoes_everything(mech):
    fake, s = mech
    eq, plate = scene(fake)

    def failing_pm():
        pm = _new_pm(fake)
        pm._fail["Location"] = RuntimeError("cannot scope")
        return pm

    fake.model.Geometry.AddPointMass = failing_pm
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False and out["rolled_back"] is True
    assert fake.model.Geometry.PointMasses == [] and fake.model.NamedSelections == []
    assert eq.Suppressed is False


def test_failure_while_suppressing_unsuppresses_the_others(mech):
    fake, s = mech
    eq, plate = scene(fake)
    eq2 = fake.add_body("EQ_other", part="Equip", mass=0.002)
    fake.add_face(eq2, 30, (0.0, 0.0, 0.02), normal=(0, 0, 1))
    eq2.CentroidZ = Q(0.01, "m")
    fake.build_geo()
    eq2._fail["Suppressed"] = RuntimeError("cannot suppress")
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False
    assert eq.Suppressed is False and eq2.Suppressed is False
    assert fake.model.Geometry.PointMasses == [] and fake.model.NamedSelections == []


def test_a_selection_with_the_same_name_gets_a_unique_name(mech):
    fake, s = mech
    eq, plate = scene(fake)
    fake.add_ns(NS, [100])  # left over from elsewhere (no point mass uses it)
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is True and out["named_selection"] == NS + "_2"
    names = [n.Name for n in fake.model.NamedSelections]
    assert len(names) == len(set(names))


def test_converting_again_after_unsuppressing_is_refused_not_double_counted(mech):
    fake, s = mech
    eq, plate = scene(fake)
    assert J(s.convert_prefix_to_point_mass("EQ_"))["ok"] is True
    eq.Suppressed = False  # the user unsuppressed the equipment
    out = J(s.convert_prefix_to_point_mass("EQ_"))
    assert out["ok"] is False and "twice" in out["error"] and out["existing_point_mass"]
    assert len(fake.model.Geometry.PointMasses) == 1 and eq.Suppressed is False
    out = J(s.convert_prefix_to_point_mass("EQ_", 3.0, True))
    assert out["ok"] is True and len(fake.model.Geometry.PointMasses) == 2


def test_nan_proximity_fails_before_the_model_is_touched(mech):
    fake, s = mech
    eq, plate = scene(fake)
    assert J(s.convert_prefix_to_point_mass("EQ_", float("nan")))["ok"] is False
    assert eq.Suppressed is False and fake.scripts == []


# -- list_point_masses in other unit systems ----------------------------------------------------------


def _pm(fake, mass, x, y, z, pin=None):
    pm = Obj(
        "PM",
        Mass=mass,
        XCoordinate=x,
        YCoordinate=y,
        ZCoordinate=z,
        PinballRegion=pin or Q(0, "mm"),
        Location=None,
    )
    fake.model.Geometry.PointMasses.append(pm)
    return pm


@pytest.mark.parametrize(
    "mass,x",
    [
        (Q(4.008, "g"), Q(10.0, "cm")),  # StandardCGS
        (Q(0.004008, "kg"), Q(100.0, "mm")),  # StandardNMM
        (Q(0.00883594, "lbm"), Q(3.937007874, "in")),  # US
        (Q(0.004008, "kg"), Q(0.1, "m")),  # SI
    ],
)
def test_point_mass_listing_is_right_in_every_unit_system(mech, mass, x):
    """The old helper converted only mm and cm: 4.008 g was reported as 4.008 kg."""
    fake, s = mech
    _pm(fake, mass, x, Q(0, "mm"), Q(0, "mm"))
    out = J(s.list_point_masses())
    pm = out["point_masses"][0]
    assert pm["mass_kg"] == pytest.approx(0.004008, rel=1e-4)
    assert pm["cg_mm"][0] == pytest.approx(100.0, abs=0.2)
