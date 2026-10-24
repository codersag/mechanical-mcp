import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import acp_wb_scripts as w  # noqa: E402


def test_script_compiles_and_ends_with_wbupdate():
    s = w.build_acp_script(
        material="Epoxy Carbon UD (230 GPa) Prepreg",
        angles=[45, 0, -45, 90],
        log_path=r"C:\Temp\x.log",
    )
    compile(s, "generated", "exec")
    assert "m.wbupdate()" in s and s.index("m.update()") < s.index("m.wbupdate()")
    assert "'plies': [[45.0, 'ply', 1], [0.0, 'ply', 1], [-45.0, 'ply', 1], [90.0, 'ply', 1]]" in s
    assert "create_material" not in s  # ACP cannot create materials inside Workbench


def test_empty_angles_rejected():
    try:
        w.build_acp_script(material="m", angles=[])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_workbench_commands():
    c = w.workbench_commands(
        acp_system="ACP-Pre",
        acp_setup="Setup 4",
        script_path=r"C:\a\b.py",
        mechanical_system="SYS 8",
        mechanical_model="Model 9",
    )
    compile(c, "wb", "exec")
    assert "RunScript(ScriptPath=" in c and "TransferData(TargetComponent=" in c
    assert c.index("Update(AllDependencies=True)") < c.index("RunScript")


def test_ply_split_snippet():
    s = w.ply_split_snippet([45, 0, -45, 90, None])
    assert s.count("ESEL,S,SECN,,") == 5 and "ESEL,S,SECN,,3" in s and "ESEL,S,SECN,,7" in s
    assert "TBDATA,1,-45,0,1," in s and "TBDATA,1,0,1,1," in s  # bulk layer flagged isotropic
    assert s.count("MPCOPY,,1,") == 5 and "EMODIF,ALL,ESYS,25" in s


def test_multi_fabric_plies():
    s = w.build_acp_script(
        fabrics={"c": ("Carbon", 0.125e-3), "b": ("Glass", 1.25e-3)},
        plies=[(45, "c", 1), (0, "c", 1), (0, "b", 2)],
    )
    compile(s, "generated", "exec")
    assert "'b': ['Glass', 0.00125]" in s and "[0.0, 'b', 2]" in s
    try:
        w.build_acp_script(fabrics={"c": ("Carbon", 1e-4)}, plies=[(0, "zzz", 1)])
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown fabric")


def test_ply_split_by_depth():
    layers = [(a, 0.125e-3) for a in (45, 0, -45, 90)] + [(None, 1.25e-3)]
    s = w.ply_split_by_depth_snippet(layers)
    assert (
        s.count("MPCOPY,,1,") == 4 and s.count("MPCOPY,,9,") == 1
    )  # carbon plies (body material) vs bulk
    assert "ESEL,S,CENT,Z,0.000125,0.00025" in s and "ESEL,A,CENT,Z,-0.00025,-0.000125" in s
    assert "TBDATA,1,0,1,1," in s  # bulk is isotropic


def test_bulk_property_block():
    s = w.ply_split_by_depth_snippet(
        [(0, 1e-3), (None, 1e-3)],
        bulk_props={"KXX": [1, 2, 3, 4, 5, 6, 7]},
        bulk_temps=list(range(7)),
    )
    assert "MPDATA,KXX,9,1,1,2,3,4,5,6" in s and "MPDATA,KXX,9,7,7" in s
    assert s.index("MPDATA,KXX,9") < s.index("MPCOPY,,9,")
