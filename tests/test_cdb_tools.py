"""Unit tests for acp_cdb_tools on a tiny synthetic ACP-style CDB (no ANSYS needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acp_cdb_tools as t  # noqa: E402


def _tiny_cdb() -> str:
    """Two stacked hexahedra (z = 0..-1 mm and -1..-2 mm) on a 10 mm x 10 mm patch."""
    xs = [-0.005, 0.005]
    nodes = []
    nid = 1
    for z in (0.0, -0.001, -0.002):
        for y in xs:
            for x in xs:
                nodes.append((nid, x, y, z))
                nid += 1
    def nline(n, x, y, z):
        return "%9d%9d%9d%16.9e%16.9e%16.9e" % (n, 0, 0, x, y, z)
    # element 1: nodes 1-4 (z=0) + 5-8 (z=-1mm); element 2: nodes 5-8 + 9-12
    def eline(eid, n):
        return "".join("%10d" % v for v in [3, 185, 1, 1, 11, 0, 0, 0, 8, 0, eid] + n)
    L = ["/PREP7", "NBLOCK,6,SOLID", "(3i9,6e16.9)"]
    L += [nline(*n) for n in nodes]
    L += ["N,R5.3,LOC,     -1,", "EBLOCK,19,SOLID,         2", "(19i10)",
          eline(1, [1, 2, 4, 3, 5, 6, 8, 7]), eline(2, [5, 6, 8, 7, 9, 10, 12, 11]), "        -1"]
    L += ["CMBLOCK,solid_P1L1__ply01_45,ELEM,         1", "(8i10)", "         1",
          "CMBLOCK,solid_P1L1__bulk,ELEM,         1", "(8i10)", "         2", "/GOPR"]
    return "\n".join(L) + "\n"


def test_inspect(tmp_path):
    p = tmp_path / "a.cdb"
    p.write_text(_tiny_cdb())
    info = t.inspect_cdb(p)
    assert info["nodes"] == 12 and info["elements"] == 2
    assert info["bbox_m"]["z"] == [-0.002, 0.0]
    assert info["element_components"] == {"solid_P1L1__ply01_45": 1, "solid_P1L1__bulk": 1}


def test_ply_angle_of_component():
    assert t.ply_angle_of_component("solid_P1L1__ply03_-45") == -45.0
    assert t.ply_angle_of_component("solid_P1L1__ply12_90") == 90.0
    assert t.ply_angle_of_component("solid_P1L1__bulk") is None


def test_prepare_external_model(tmp_path):
    src, dst = tmp_path / "a.cdb", tmp_path / "b.cdb"
    src.write_text(_tiny_cdb())
    res = t.prepare_external_model(src, dst, strike_radius_m=0.0075)
    assert res["angle_to_material_esys"] == {"45.0": {"material": 11, "esys": 21}}
    assert res["unassigned_elements"] == 0
    assert res["node_sets"]["STRIKE"] == 4          # four top-surface nodes within r <= 7.5 mm
    info = t.inspect_cdb(dst)
    assert info["materials"] == {2: 1, 11: 1}       # ply element -> mat 11, bulk element -> mat 2
    assert info["element_types"] == {1: 2}
    assert info["coordinate_systems"] == {0: 1, 21: 1}
    assert "STRIKE" in info["node_components"] and "GNDNODES" in info["node_components"]
    assert "LOCAL,R5.0,ANG,21,0,45,0,0" in dst.read_text()
