"""Example: build the Ogasawara-style plate layup through the MCP tool functions and export it.

Usage: python examples/build_plate_layup.py <shell.cdb> <out_dir>
(12 plies [45/0/-45/90]x3 of 0.125 mm + a 2.5 mm bulk layer in 2 elements; the strike face is z = 0.)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acp_mcp_server as s  # noqa: E402


def check(label, raw):
    d = json.loads(raw)
    print("%-14s %s" % (label, "ok" if d.get("ok") else "FAILED: %s" % d))
    if not d.get("ok"):
        raise SystemExit(1)
    return d


def main(shell_cdb: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    check("launch", s.acp_launch())
    try:
        check("import", s.acp_import_model(shell_cdb, "plate", "ansys:cdb", "si"))
        check("material", s.acp_create_material("plate", "cfrp", 1.2e11, 9e9, 9e9, .3, .3, .4, 5e9, 5e9, 3e9, 1520.0))
        check("fabric", s.acp_create_fabric("plate", "ply_0p125", "cfrp", 0.125e-3))
        check("bulk fabric", s.acp_create_fabric("plate", "bulk_1p25", "cfrp", 1.25e-3))
        check("rosette", s.acp_create_rosette("plate", "rosette_xy"))
        check("oss", s.acp_create_oriented_selection_set("plate", "oss", ["All_Elements"], ["rosette_xy"], [0, 0, -1]))
        check("layup", s.acp_create_layup("plate", "layup", "ply_0p125", [45, 0, -45, 90] * 3, "oss", "bulk_1p25", 2))
        check("solid model", s.acp_create_solid_model("plate", "solid", ["oss"]))
        print(check("update", s.acp_update("plate"))["solid_models"])
        check("export", s.acp_export_solid_model("plate", "solid", str(out / "plate_solid.cdb")))
        print(json.dumps(check("convert", s.acp_prepare_external_model(str(out / "plate_solid.cdb"), str(out / "plate_ext.cdb"))), indent=1))
        check("save", s.acp_save_model("plate", str(out / "plate.acph5")))
    finally:
        s.acp_shutdown()


if __name__ == "__main__":
    os.environ.setdefault("AWP_ROOT261", r"C:\Program Files\ANSYS Inc\v261")
    main(sys.argv[1], sys.argv[2])
