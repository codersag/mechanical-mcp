"""Pure-Python helpers for ANSYS CDB files exported by ACP (no ANSYS needed).

Know-how captured from the lightning-strike CFRP project:

* An ACP *solid model* CDB uses layered SOLID185 (one element type per ply section) and
  stores ply identity only as element components named ``<solid>_P<n>L<m>__<ply name>``
  (e.g. ``solid_P1L1__ply03_-45``).  There are no per-ply materials and every element
  shares one coordinate system, so a thermal-electric / coupled-field solve cannot use it
  directly.
* ``prepare_external_model`` rewrites the element block so every distinct fibre angle gets
  its own material id and its own element coordinate system, adds the matching ``LOCAL``
  systems, makes all elements homogeneous (type 1) and appends two node components
  (a strike patch on the top surface and the grounded side faces).  The result imports
  cleanly through Workbench "External Model" into Mechanical.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

_PLY_RE = re.compile(r"_ply\d+_(-?\d+(?:\.\d+)?)$", re.IGNORECASE)
_BULK_RE = re.compile(r"bulk$", re.IGNORECASE)
_I10 = 10


def _read_lines(path: str | Path) -> list[str]:
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def _find(lines: list[str], prefix: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i].startswith(prefix):
            return i
    return -1


def _block_end(lines: list[str], first_data: int) -> int:
    """Index of the '-1' terminator of an NBLOCK/EBLOCK starting at first_data."""
    j = first_data
    while j < len(lines):
        tok = lines[j].split()
        if (tok and tok[0] == "-1") or lines[j].startswith(
            "N,"
        ):  # ACP ends NBLOCK with "N,R5.3,LOC,-1,"
            return j
        j += 1
    raise ValueError("unterminated block")


def parse_nodes(lines: list[str]) -> dict[int, tuple[float, float, float]]:
    i = _find(lines, "NBLOCK")
    if i < 0:
        return {}
    m = re.search(r"e(\d+)", lines[i + 1], re.IGNORECASE)  # e.g. (3i9,6e16.9) or (3i9,6e21.13e3)
    w = int(m.group(1)) if m else 21
    end = _block_end(lines, i + 2)
    nodes = {}
    for ln in lines[i + 2 : end]:
        nid = int(ln[0:9])
        nodes[nid] = tuple(_tofloat(ln[27 + w * k : 27 + w * (k + 1)]) for k in range(3))
    return nodes


def _tofloat(tok: str) -> float:
    tok = tok.strip()
    try:
        return float(tok)
    except ValueError:
        m = re.match(r"^(-?[0-9.]+)([+-][0-9]{3})$", tok)
        return float(m.group(1) + "E" + m.group(2)) if m else 0.0


def parse_elements(lines: list[str]) -> tuple[int, int, list[list[int]]]:
    """Return (EBLOCK line index, terminator index, rows). Each row = 19 ints for a hexa8."""
    i = _find(lines, "EBLOCK")
    if i < 0:
        raise ValueError("no EBLOCK found")
    end = _block_end(lines, i + 2)
    rows = [[int(x) for x in ln.split()] for ln in lines[i + 2 : end]]
    return i, end, rows


def parse_element_components(lines: list[str]) -> dict[str, list[int]]:
    comps: dict[str, list[int]] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("CMBLOCK,") and ",ELEM," in ln.upper():
            name = ln.split(",")[1].strip()
            n = int(ln.split(",")[3].split("!")[0])
            per = int(lines[i + 1].strip().strip("()").lower().split("i")[0])
            nrows = (n + per - 1) // per
            ids = []
            for r in lines[i + 2 : i + 2 + nrows]:
                ids += [int(x) for x in r.split()]
            comps[name] = ids[:n]
            i += 2 + nrows
        else:
            i += 1
    return comps


def inspect_cdb(path: str | Path) -> dict:
    """Summarise an ACP/MAPDL CDB: counts, element types, materials, sections, components."""
    lines = _read_lines(path)
    nodes = parse_nodes(lines)
    _, _, rows = parse_elements(lines)
    comps = parse_element_components(lines)
    node_comps = [
        ln.split(",")[1].strip()
        for ln in lines
        if ln.startswith("CMBLOCK,") and ",NODE," in ln.upper()
    ]
    zs = [v[2] for v in nodes.values()] or [0.0]
    xs = [v[0] for v in nodes.values()] or [0.0]
    ys = [v[1] for v in nodes.values()] or [0.0]

    def count(idx):
        out: dict[int, int] = {}
        for r in rows:
            out[r[idx]] = out.get(r[idx], 0) + 1
        return dict(sorted(out.items()))

    return {
        "nodes": len(nodes),
        "elements": len(rows),
        "bbox_m": {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)], "z": [min(zs), max(zs)]},
        "element_types": count(1),
        "materials": count(0),
        "sections": count(3),
        "coordinate_systems": count(4),
        "element_components": {k: len(v) for k, v in comps.items()},
        "node_components": node_comps,
        "units": next((ln.strip() for ln in lines if ln.startswith("/UNITS")), ""),
    }


def ply_angle_of_component(name: str) -> float | None:
    """Angle encoded in an ACP ply component name, or None if it is not a ply component."""
    m = _PLY_RE.search(name)
    return float(m.group(1)) if m else None


def _fmt_ids(ids: list[int], per: int = 8) -> list[str]:
    return ["".join("%10d" % v for v in ids[k : k + per]) for k in range(0, len(ids), per)]


def _local_block(csid: int, angle_deg: float) -> list[str]:
    """Coordinate system rotated about global Z, written in the same ``LOCAL,R5.0`` style ACP itself uses."""
    return [
        "LOCAL,R5.0,LOC,%d,0,0,0,0" % csid,
        "LOCAL,R5.0,ANG,%d,0,%g,0,0" % (csid, angle_deg),
        "LOCAL,R5.0,PRM,%d,0,1.0,1.0" % csid,
    ]


def prepare_external_model(
    src: str | Path,
    dst: str | Path,
    *,
    ply_mat_base: int = 11,
    bulk_mat: int = 2,
    esys_base: int = 21,
    strike_radius_m: float = 0.005,
    ground_bottom: bool = False,
    make_node_sets: bool = True,
) -> dict:
    """Convert an ACP solid-model CDB into a homogeneous per-angle external-model CDB.

    Every distinct ply angle gets material ``ply_mat_base + k`` and element coordinate
    system ``esys_base + k`` (rotation about global Z by the angle).  Elements of
    components matching ``*bulk`` get material ``bulk_mat`` and ESYS 0.  Element type and
    section are reset to 1 so every element is a plain homogeneous hexahedron.

    Returns a JSON-able dict with the angle -> (material, esys) mapping and the counts.
    """
    lines = _read_lines(src)
    comps = parse_element_components(lines)
    ei, eend, rows = parse_elements(lines)

    angles = sorted({a for a in (ply_angle_of_component(n) for n in comps) if a is not None})
    amap = {a: (ply_mat_base + k, esys_base + k) for k, a in enumerate(angles)}
    elem_attr: dict[int, tuple[int, int]] = {}
    for name, ids in comps.items():
        a = ply_angle_of_component(name)
        if a is not None:
            for e in ids:
                elem_attr[e] = amap[a]
        elif _BULK_RE.search(name):
            for e in ids:
                elem_attr[e] = (bulk_mat, 0)

    new_rows = []
    unassigned = 0
    for r in rows:
        eid = r[10]
        mat, esys = elem_attr.get(eid, (r[0], r[4]))
        if eid not in elem_attr:
            unassigned += 1
        r = list(r)
        r[0], r[1], r[3], r[4] = mat, 1, 0, esys
        new_rows.append("".join("%9d" % v for v in r))

    out = lines[: ei + 2] + new_rows + lines[eend:]
    # LOCAL systems go right before the EBLOCK (after the ACP "CSYS,0")
    local_lines: list[str] = []
    for a in angles:
        local_lines += _local_block(amap[a][1], a)
    local_lines.append("CSYS,0")
    out = out[:ei] + local_lines + out[ei:]

    node_sets = {}
    if make_node_sets:
        nodes = parse_nodes(lines)
        if nodes:
            ztop = max(v[2] for v in nodes.values())
            zbot = min(v[2] for v in nodes.values())
            xmax = max(abs(v[0]) for v in nodes.values())
            ymax = max(abs(v[1]) for v in nodes.values())
            tol = 1e-9
            strike = sorted(
                n
                for n, v in nodes.items()
                if abs(v[2] - ztop) < tol and math.hypot(v[0], v[1]) <= strike_radius_m + tol
            )
            ground = sorted(
                n
                for n, v in nodes.items()
                if abs(abs(v[0]) - xmax) < 1e-6
                or abs(abs(v[1]) - ymax) < 1e-6
                or (ground_bottom and abs(v[2] - zbot) < tol)
            )
            node_sets = {"STRIKE": strike, "GNDNODES": ground}
            add: list[str] = []
            for nm, ids in node_sets.items():
                add.append(
                    "CMBLOCK,%-8s,NODE,%8d  ! users node component definition" % (nm, len(ids))
                )
                add.append("(8i10)")
                add += _fmt_ids(ids)
            gi = max((k for k, ln in enumerate(out) if ln.startswith("/GOPR")), default=len(out))
            out = out[:gi] + add + out[gi:]

    Path(dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    return {
        "angle_to_material_esys": {
            str(a): {"material": m, "esys": e} for a, (m, e) in amap.items()
        },
        "bulk_material": bulk_mat,
        "elements": len(rows),
        "unassigned_elements": unassigned,
        "node_sets": {k: len(v) for k, v in node_sets.items()},
        "output": str(dst),
    }


def normalise_with_mapdl(
    src: str | Path, dst: str | Path, *, ansys_exe: str | None = None, np: int = 2
) -> str:
    """Re-write a CDB through MAPDL (CDREAD + CDWRITE) so Workbench's External Model reader accepts it.

    Needs an ANSYS install (ANSYS261.exe under %AWP_ROOT261%).  Returns the output path.
    """
    import os
    import subprocess
    import tempfile

    root = os.environ.get("AWP_ROOT261", r"C:\Program Files\ANSYS Inc\v261")
    exe = ansys_exe or os.path.join(root, "ansys", "bin", "winx64", "ANSYS261.exe")
    src, dst = Path(src).resolve(), Path(dst).resolve()
    final, dst = dst, dst.with_name(dst.stem + "_norm.cdb")
    dst.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "norm.dat"
        inp.write_text(
            "\n".join(
                [
                    "/BATCH",
                    "/PREP7",
                    "CDREAD,DB,'%s','cdb'" % src.with_suffix("").as_posix(),
                    "CDWRITE,DB,'%s','cdb'" % dst.with_suffix("").as_posix(),
                    "FINISH",
                    "",
                ]
            )
        )
        subprocess.run(
            [
                exe,
                "-b",
                "-smp",
                "-np",
                str(np),
                "-dir",
                td,
                "-i",
                str(inp),
                "-o",
                str(Path(td) / "norm.out"),
            ],
            check=False,
            capture_output=True,
        )
    if not dst.exists():
        raise RuntimeError("MAPDL normalisation produced no file")
    dst.replace(final)
    return str(final)
