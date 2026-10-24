"""MCP server for ANSYS Composite PrepPost (ACP) via PyACP (ansys-acp-core).

Gives an AI client a scripted, repeatable way to build and export composite layups:
launch ACP, import a shell mesh, define materials / fabrics / rosettes / oriented selection
sets / modeling groups / plies, build the ACP solid model, export it, and convert the export
into a clean per-fibre-angle external model for a Mechanical coupled-field solve.

Tested with ACP 2026 R1 and ansys-acp-core 0.3.x.
"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This server targets the mcp 1.x API; mcp 2.x renamed FastMCP to MCPServer.\n"
        "Install with: pip install 'mcp[cli]>=1.0,<2'"
    ) from exc

import acp_cdb_tools as cdb
import mcp_paths as paths

mcp = FastMCP("ansys-acp-mcp")

_STATE: dict = {"acp": None, "models": {}}


# --------------------------------------------------------------------------- helpers
_ENV = ("ACP_MCP_ROOTS", "MECHANICAL_MCP_ROOTS")
_READ_EXTS = {".cdb", ".h5", ".dat", ".acph5", ".mac", ".inp"}


class _PathError(Exception):
    """A path argument outside the allowed folders; the message is safe to show."""


def _in(path: str, exts=None) -> str:
    """An existing input file inside the allowed folders (ACP_MCP_ROOTS, else MECHANICAL_MCP_ROOTS,
    else the working directory)."""
    p, err = paths.allow(path, env_names=_ENV, exts=exts)
    if err:
        raise _PathError(err)
    return str(p)


def _out(path: str, exts=None, overwrite: bool = False) -> str:
    """A new output file inside the allowed folders; existing files are only replaced with
    overwrite=true."""
    p, err = paths.allow(path, env_names=_ENV, exts=exts, write=True, overwrite=overwrite)
    if err:
        raise _PathError(err)
    return str(p)


def _ok(**kw) -> str:
    return paths.redact(json.dumps({"ok": True, **kw}, indent=2, default=str))


def _err(msg: str, exc: Exception | None = None) -> str:
    d = {"ok": False, "error": msg}
    if isinstance(exc, _PathError):
        d["error"] = str(exc)
    elif exc is not None:
        d["detail"] = "%s: %s" % (type(exc).__name__, str(exc)[:600])
        if os.environ.get("ACP_MCP_DEBUG"):
            d["traceback"] = traceback.format_exc()
    return paths.redact(json.dumps(d, indent=2))


def _pyacp():
    import ansys.acp.core as pyacp  # imported lazily so the CDB tools work without ACP

    return pyacp


def _acp():
    if _STATE["acp"] is None:
        raise RuntimeError("ACP is not running: call acp_launch first")
    return _STATE["acp"]


def _model(name: str):
    m = _STATE["models"].get(name)
    if m is None:
        raise KeyError("unknown model %r; loaded: %s" % (name, list(_STATE["models"])))
    return m


def _find(collection, name: str):
    for obj in collection.values():
        if obj.name == name:
            return obj
    raise KeyError("no object named %r (have: %s)" % (name, [o.name for o in collection.values()]))


def _names(collection) -> list[str]:
    return [o.name for o in collection.values()]


def _unit_system(pyacp, name: str):
    key = (name or "si").upper()
    return {
        "SI": pyacp.UnitSystemType.SI,
        "MKS": pyacp.UnitSystemType.MKS,
        "UMKS": pyacp.UnitSystemType.uMKS,
        "MPA": pyacp.UnitSystemType.MPA,
        "FROM_FILE": pyacp.UnitSystemType.FROM_FILE,
    }.get(key, pyacp.UnitSystemType.SI)


# --------------------------------------------------------------------------- session
@mcp.tool()
def acp_launch(timeout_s: float = 60.0) -> str:
    """Start (or reuse) a PyACP-managed ACP server. Needs AWP_ROOT261 or the default install path."""
    try:
        if _STATE["acp"] is not None:
            return _ok(message="ACP already running", models=list(_STATE["models"]))
        pyacp = _pyacp()
        _STATE["acp"] = pyacp.launch_acp(timeout=timeout_s)
        return _ok(message="ACP launched")
    except Exception as e:
        return _err("could not launch ACP", e)


@mcp.tool()
def acp_status() -> str:
    """Report whether ACP is running and which models are loaded."""
    return _ok(running=_STATE["acp"] is not None, models=list(_STATE["models"]))


@mcp.tool()
def acp_shutdown() -> str:
    """Stop the ACP server and forget all models."""
    try:
        acp = _STATE["acp"]
        if acp is not None:
            stop = getattr(acp, "stop", None) or getattr(acp, "finish", None)
            if stop:
                stop()
        _STATE["acp"] = None
        _STATE["models"].clear()
        return _ok(message="ACP stopped")
    except Exception as e:
        _STATE["acp"] = None
        _STATE["models"].clear()
        return _err("error while stopping ACP (state cleared anyway)", e)


# --------------------------------------------------------------------------- model I/O
@mcp.tool()
def acp_import_model(
    path: str, name: str = "model", fmt: str = "ansys:cdb", unit_system: str = "si"
) -> str:
    """Import a mesh into a new ACP model.

    fmt: 'ansys:cdb' (blocked CDB), 'ansys:h5', 'ansys:dat', or 'acp:h5' (a saved ACP model).
    unit_system: si | mks | umks | mpa | from_file (ignored for 'acp:h5').
    The element set 'All_Elements' is created automatically for ANSYS meshes.
    """
    try:
        pyacp = _pyacp()
        acp = _acp()
        kwargs = {} if fmt == "acp:h5" else {"unit_system": _unit_system(pyacp, unit_system)}
        model = acp.import_model(path=_in(path, _READ_EXTS), format=fmt, **kwargs)
        _STATE["models"][name] = model
        return _ok(
            model=name, element_sets=_names(model.element_sets), rosettes=_names(model.rosettes)
        )
    except Exception as e:
        return _err("import failed", e)


@mcp.tool()
def acp_save_model(model: str, path: str, overwrite: bool = False) -> str:
    """Save an ACP model as an .acph5 file (acp:h5 format) that the ACP GUI can open."""
    try:
        path = _out(path, {".acph5", ".h5"}, overwrite)
        _model(model).save(path)
        return _ok(saved=path)
    except Exception as e:
        return _err("save failed", e)


@mcp.tool()
def acp_model_tree(model: str) -> str:
    """List the main objects of a model with their statuses."""
    try:
        m = _model(model)
        solid = {}
        for sm in m.solid_models.values():
            solid[sm.name] = {"status": str(sm.status), "analysis_plies": len(sm.analysis_plies)}
        groups = {}
        for g in m.modeling_groups.values():
            groups[g.name] = _names(g.modeling_plies)
        return _ok(
            element_sets=_names(m.element_sets),
            materials=_names(m.materials),
            fabrics=_names(m.fabrics),
            rosettes=_names(m.rosettes),
            oriented_selection_sets=_names(m.oriented_selection_sets),
            modeling_groups=groups,
            solid_models=solid,
        )
    except Exception as e:
        return _err("could not read model tree", e)


# --------------------------------------------------------------------------- definitions
@mcp.tool()
def acp_create_material(
    model: str,
    name: str,
    E1: float,
    E2: float,
    E3: float,
    nu12: float,
    nu13: float,
    nu23: float,
    G12: float,
    G31: float,
    G23: float,
    density: float = 0.0,
    ply_type: str = "regular",
) -> str:
    """Create an orthotropic ply material (SI units unless the model was imported with other units).

    Properties are placeholders when the downstream solve takes thermal / electrical behaviour from
    elsewhere (for example a user material in Mechanical).
    """
    try:
        pyacp = _pyacp()
        sets = pyacp.material_property_sets
        eng = sets.ConstantEngineeringConstants.from_orthotropic_constants(
            E1=E1, E2=E2, E3=E3, nu12=nu12, nu13=nu13, nu23=nu23, G12=G12, G31=G31, G23=G23
        )
        kw = {"engineering_constants": eng}
        if density:
            kw["density"] = sets.ConstantDensity(rho=density)
        pt = {
            "regular": pyacp.PlyType.REGULAR,
            "woven": pyacp.PlyType.WOVEN,
            "isotropic": pyacp.PlyType.ISOTROPIC,
        }.get(ply_type.lower(), pyacp.PlyType.REGULAR)
        _model(model).create_material(name=name, ply_type=pt, **kw)
        return _ok(created=name)
    except Exception as e:
        return _err("create_material failed", e)


@mcp.tool()
def acp_create_fabric(model: str, name: str, material: str, thickness: float) -> str:
    """Create a fabric (one ply layer) from a material. thickness is in model length units."""
    try:
        m = _model(model)
        m.create_fabric(name=name, material=_find(m.materials, material), thickness=thickness)
        return _ok(created=name)
    except Exception as e:
        return _err("create_fabric failed", e)


@mcp.tool()
def acp_create_rosette(
    model: str,
    name: str,
    origin: list[float] | None = None,
    dir1: list[float] | None = None,
    dir2: list[float] | None = None,
) -> str:
    """Create a parallel rosette. dir1 is the 0-degree reference direction."""
    try:
        _model(model).create_rosette(
            name=name,
            origin=tuple(origin or (0, 0, 0)),
            dir1=tuple(dir1 or (1, 0, 0)),
            dir2=tuple(dir2 or (0, 1, 0)),
        )
        return _ok(created=name)
    except Exception as e:
        return _err("create_rosette failed", e)


@mcp.tool()
def acp_create_oriented_selection_set(
    model: str,
    name: str,
    element_sets: list[str],
    rosettes: list[str],
    orientation_point: list[float],
    orientation_direction: list[float] | None = None,
) -> str:
    """Create an oriented selection set. The laminate is extruded toward orientation_point.

    Example: shell mesh at z=0 and orientation_point (0,0,-1) puts the plies below the surface
    (the first ply listed in a modeling group sits against the reference surface).
    """
    try:
        m = _model(model)
        m.create_oriented_selection_set(
            name=name,
            element_sets=[_find(m.element_sets, n) for n in element_sets],
            rosettes=[_find(m.rosettes, n) for n in rosettes],
            orientation_point=tuple(orientation_point),
            orientation_direction=tuple(orientation_direction or orientation_point),
        )
        return _ok(created=name)
    except Exception as e:
        return _err("create_oriented_selection_set failed", e)


@mcp.tool()
def acp_create_layup(
    model: str,
    group: str,
    fabric: str,
    angles: list[float],
    oriented_selection_set: str,
    bulk_fabric: str = "",
    bulk_layers: int = 0,
) -> str:
    """Create a modeling group with one ply per angle (in order from the reference surface).

    Plies are named plyNN_<angle> (the name encodes the angle, which acp_prepare_external_model
    relies on). Optionally appends a 'bulk' ply of bulk_layers layers of bulk_fabric.
    """
    try:
        m = _model(model)
        fab = _find(m.fabrics, fabric)
        oss = _find(m.oriented_selection_sets, oriented_selection_set)
        mg = m.create_modeling_group(name=group)
        for i, ang in enumerate(angles, 1):
            label = int(ang) if float(ang).is_integer() else ang
            mg.create_modeling_ply(
                name="ply%02d_%s" % (i, label),
                ply_material=fab,
                oriented_selection_sets=[oss],
                ply_angle=float(ang),
                number_of_layers=1,
            )
        if bulk_fabric and bulk_layers > 0:
            mg.create_modeling_ply(
                name="bulk",
                ply_material=_find(m.fabrics, bulk_fabric),
                oriented_selection_sets=[oss],
                ply_angle=0.0,
                number_of_layers=int(bulk_layers),
            )
        return _ok(group=group, plies=len(angles) + (1 if bulk_fabric and bulk_layers > 0 else 0))
    except Exception as e:
        return _err("create_layup failed", e)


@mcp.tool()
def acp_create_solid_model(
    model: str,
    name: str,
    oriented_selection_sets: list[str],
    extrusion_method: str = "analysis_ply_wise",
) -> str:
    """Create a solid model from oriented selection sets (analysis_ply_wise gives one element layer per ply)."""
    try:
        pyacp = _pyacp()
        m = _model(model)
        method = {
            "analysis_ply_wise": pyacp.ExtrusionMethod.ANALYSIS_PLY_WISE,
            "monolithic": pyacp.ExtrusionMethod.MONOLITHIC,
            "modeling_ply_wise": pyacp.ExtrusionMethod.MODELING_PLY_WISE,
            "material_wise": pyacp.ExtrusionMethod.MATERIAL_WISE,
        }[extrusion_method]
        m.create_solid_model(
            name=name,
            element_sets=[_find(m.oriented_selection_sets, n) for n in oriented_selection_sets],
            extrusion_method=method,
        )
        return _ok(created=name)
    except Exception as e:
        return _err("create_solid_model failed", e)


@mcp.tool()
def acp_update(model: str) -> str:
    """Update (recompute) the model; reports each solid model's status and analysis-ply count."""
    try:
        m = _model(model)
        m.update()
        return _ok(
            solid_models={
                sm.name: {"status": str(sm.status), "analysis_plies": len(sm.analysis_plies)}
                for sm in m.solid_models.values()
            }
        )
    except Exception as e:
        return _err("update failed", e)


@mcp.tool()
def acp_export_solid_model(
    model: str, solid_model: str, path: str, fmt: str = "ansys:cdb", overwrite: bool = False
) -> str:
    """Export a solid model to CDB ('ansys:cdb') or H5 ('ansys:h5')."""
    try:
        pyacp = _pyacp()
        m = _model(model)
        path = _out(path, {".cdb", ".h5"}, overwrite)
        f = (
            pyacp.SolidModelExportFormat.ANSYS_CDB
            if fmt.endswith("cdb")
            else pyacp.SolidModelExportFormat.ANSYS_H5
        )
        _find(m.solid_models, solid_model).export(path, format=f)
        return _ok(exported=path, size_bytes=Path(path).stat().st_size)
    except Exception as e:
        return _err("export failed", e)


# --------------------------------------------------------------------------- CDB utilities (no ACP needed)
@mcp.tool()
def acp_inspect_cdb(path: str) -> str:
    """Summarise a CDB: node/element counts, bounding box (m), element types, materials, sections,
    element coordinate systems, element and node component names with sizes."""
    try:
        return _ok(**cdb.inspect_cdb(_in(path, {".cdb", ".dat", ".mac"})))
    except Exception as e:
        return _err("inspect failed", e)


@mcp.tool()
def acp_prepare_external_model(
    src: str,
    dst: str,
    strike_radius_mm: float = 5.0,
    ply_mat_base: int = 11,
    bulk_mat: int = 2,
    esys_base: int = 21,
    ground_bottom: bool = True,
    normalise_with_mapdl: bool = True,
    node_boxes_json: str = "",
    overwrite: bool = False,
) -> str:
    """Convert an ACP solid-model CDB into a per-fibre-angle external model for Mechanical.

    Each distinct ply angle gets its own material id and element coordinate system; elements become
    homogeneous hexahedra; node components STRIKE (top surface within strike_radius_mm of the centre)
    and GNDNODES (the lateral faces, optionally the bottom) are added. Import the result through a
    Workbench External Model system. The returned mapping tells you which material id carries which
    fibre angle (needed when assigning properties in Mechanical).

    normalise_with_mapdl (default on) re-writes the file through MAPDL, which Workbench's External Model
    reader needs; turn it off only if ANSYS is not installed. ground_bottom adds the bottom face to GNDNODES.

    node_boxes_json replaces STRIKE / GNDNODES with your own node components: a JSON object
    {"NAME": [xmin, xmax, ymin, ymax, zmin, zmax]} in metres, null = open side, e.g.
    {"INNER_WALL": [null, null, null, null, 0.125, 0.125]}.
    """
    try:
        src = _in(src, {".cdb", ".dat"})
        dst = _out(dst, {".cdb", ".dat"}, overwrite)
        boxes = json.loads(node_boxes_json) if node_boxes_json.strip() else None
        res = cdb.prepare_external_model(
            src,
            dst,
            strike_radius_m=strike_radius_mm / 1000.0,
            ply_mat_base=ply_mat_base,
            bulk_mat=bulk_mat,
            esys_base=esys_base,
            ground_bottom=ground_bottom,
            node_boxes=boxes,
        )
        if normalise_with_mapdl:
            cdb.normalise_with_mapdl(dst, dst)
            res["normalised_with_mapdl"] = True
        return _ok(**res)
    except Exception as e:
        return _err("conversion failed", e)


# --------------------------------------------------------------------------- Workbench-embedded ACP (RunScript route)
import acp_wb_scripts as wbs  # noqa: E402


@mcp.tool()
def acp_wb_build_script(
    output_path: str,
    material: str,
    angles: list,
    layers: int = 1,
    ply_thickness_mm: float = 0.125,
    fabric: str = "ply",
    group: str = "mg",
    oss: str = "oss",
    element_set: str = "All_Elements",
    solid_model: str = "solid",
    ex_type: str = "analysis_ply_wise",
    log_path: str = "",
    overwrite: bool = False,
) -> str:
    """Write the script that builds a layup inside WORKBENCH's own ACP (legacy API) and ends with wbupdate().

    `material` must already exist in the ACP-Pre system's Engineering Data (import a composite material there first).
    Run the file with the Workbench commands returned by acp_wb_commands. Nothing is executed here.
    """
    try:
        output_path = _out(output_path, {".py", ".wbjn"}, overwrite)
        log = log_path or str(Path(output_path).with_suffix(".log"))
        log = _out(log, {".log", ".txt"}, True)
        p = wbs.write_build_script(
            output_path,
            material=material,
            angles=[float(a) for a in angles],
            layers=layers,
            ply_thickness_m=ply_thickness_mm / 1000.0,
            fabric=fabric,
            group=group,
            oss=oss,
            element_set=element_set,
            solid_model=solid_model,
            ex_type=ex_type,
            log_path=log,
        )
        return _ok(script=p, log_file=log)
    except Exception as e:
        return _err("could not write ACP build script", e)


@mcp.tool()
def acp_wb_commands(
    script_path: str,
    acp_system: str = "ACP-Pre",
    acp_setup: str = "Setup",
    mechanical_system: str = "",
    mechanical_model: str = "",
) -> str:
    """Return the Workbench scripting commands that run an ACP script via Setup.RunScript and, optionally, link the
    ACP Setup cell to a Mechanical Model cell with TransferData. Paste them into a Workbench scripting session
    (for example the ansys-workbench MCP's execute_workbench_script). For Thermal-Electric Mechanical systems the
    transferred solid is one SOLID225 body whose plies are identifiable by section number; use acp_wb_ply_split_snippet.
    """
    try:
        script_path = _in(script_path, {".py", ".wbjn"})
        return _ok(
            commands=wbs.workbench_commands(
                acp_system=acp_system,
                acp_setup=acp_setup,
                script_path=script_path,
                mechanical_system=mechanical_system or None,
                mechanical_model=mechanical_model or None,
            )
        )
    except Exception as e:
        return _err("could not build commands", e)


@mcp.tool()
def acp_wb_ply_split_snippet(angles: list, first_section: int = 3) -> str:
    """Return the Mechanical command snippet that splits an ACP-transferred Thermal-Electric solid into plies by section
    number (own material copy, ESYS and TB,USER per ply). Use null for an isotropic bulk layer. Verified on a 4-ply test:
    sections 3..6 were exactly one ply (672 elements) each."""
    try:
        return _ok(
            snippet=wbs.ply_split_snippet(
                [None if a is None else float(a) for a in angles], first_section=first_section
            )
        )
    except Exception as e:
        return _err("could not build snippet", e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
