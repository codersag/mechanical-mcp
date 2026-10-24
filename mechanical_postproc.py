"""Post-processing on exported nodal data: line sampling and membrane + bending linearization.

Pure functions on plain lists; numpy and scipy are imported lazily (install the `postproc` extra).
Every failure is a ValueError with a message, never a ZeroDivisionError, IndexError or QhullError.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Dict, Sequence

#: Largest export accepted (nodes). Override with MECHANICAL_MCP_MAX_NODES.
MAX_NODES = int(os.environ.get("MECHANICAL_MCP_MAX_NODES", "1000000"))
#: A sample is "in a gap" when the triangulation simplex it falls in has a longest edge more than
#: this many times the median longest edge of the simplices around it (a hole or notch is bridged
#: by a few huge simplices among many small ones; a uniformly long or graded mesh is not).
GAP_RATIO = 3.0
#: ... and fewer than this fraction of the simplices around it are of similar size (otherwise it is
#: a coarse region next to a fine one, a size step, not a void).
GAP_SIMILAR_FRACTION = 0.2

#: Marks in a result name that suggest an unsigned quantity, for export files that do not say.
_UNSIGNED_NAME = re.compile(
    r"equivalent|von\s*mises|intensity|total\s+deformation|safety|ratio", re.I
)


class GapError(ValueError):
    """The section line crosses a void. `diagnostics` has the largest span and where it is."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def _merge_coincident(pts, vals, tol):
    """Merge nodes closer than tol (interface nodes of touching bodies share a position): their
    values are averaged. Without this the 'node spacing' of such a node is ~0."""
    import numpy as np

    key = np.round(pts / tol).astype(np.int64)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    if len(counts) == len(pts):
        return pts, vals, 0, []
    mp = np.zeros((len(counts), pts.shape[1]))
    mv = np.zeros(len(counts))
    lo = np.full(len(counts), np.inf)
    hi = np.full(len(counts), -np.inf)
    np.add.at(mp, inv, pts)
    np.add.at(mv, inv, vals)
    np.minimum.at(lo, inv, vals)
    np.maximum.at(hi, inv, vals)
    mp, mv = mp / counts[:, None], mv / counts
    # coincident nodes that carry DIFFERENT values (a stress jump across a bonded interface):
    # averaging them hides the jump, so say so
    scale = max(float(np.max(np.abs(vals))), 1e-30)
    jumps = np.nonzero((counts > 1) & ((hi - lo) > 1e-6 * scale))[0]
    conflicts = [
        {"position": [float(x) for x in mp[k]], "values": [float(lo[k]), float(hi[k])]}
        for k in jumps[:5]
    ]
    if len(jumps) > 5:
        conflicts.append({"more": int(len(jumps) - 5)})
    return mp, mv, len(pts) - len(counts), conflicts


def sample_line(
    nodes: Sequence[Sequence[float]],
    values: Sequence[float],
    a,
    b,
    n: int = 81,
    allow_gaps=False,
    snap_fraction: float = 0.25,
    cancel=None,
):
    """Linear interpolation of nodal `values` at n points from a to b.

    Returns (distance, sampled values, gaps, info). Coincident nodes are merged first. The local
    element size at a sample is the median distance from the nearest node to its 6 nearest
    simplices, so graded, polar and anisotropic meshes are judged by their own scale. A sample is
    "in a gap" when the triangulation simplex it falls in is more than GAP_RATIO times larger
    (longest edge) than the median of the simplices around it: a hole or notch inside the convex
    hull is bridged by a few huge simplices among many small ones and would otherwise be
    interpolated straight across. Gaps are a GapError unless allow_gaps.

    Only the nodes near the line are triangulated (k-d tree). A sample that falls outside the
    triangulation by less than snap_fraction of a simplex (a line end on a curved boundary, or a
    hair outside a flat face) is extrapolated from the nearest simplex and listed in
    info["endpoint_adjustments"]; further out the line is reported as leaving the mesh.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 2:
        raise ValueError("samples must be an integer of at least 2")
    d = len(a)
    if len(b) != d or d not in (2, 3):
        raise ValueError("start and end must both have 2 or 3 coordinates")
    length = math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(d)))
    if not length > 1e-12:
        raise ValueError("start and end points are the same: the section line has no length")
    import numpy as np
    from scipy.spatial import Delaunay, cKDTree

    pts = np.asarray(nodes, float)
    vals = np.asarray(values, float)
    if pts.ndim != 2 or pts.shape[1] != d:
        raise ValueError("nodes must have %d coordinates each" % d)
    diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise RuntimeError("cancelled")

    check_cancel()
    pts, vals, merged, conflicts = _merge_coincident(pts, vals, max(1e-9 * diag, 1e-12))
    if len(pts) < d + 1:
        raise ValueError("need at least %d nodes to interpolate" % (d + 1))
    s = [length * i / (n - 1) for i in range(n)]
    pos = np.array([[a[k] + (b[k] - a[k]) * i / (n - 1) for k in range(d)] for i in range(n)])

    tree = cKDTree(pts)
    d1, i1 = tree.query(pos, k=1)
    kk = min(7, len(pts))
    nn, _ = tree.query(pts[i1], k=kk)
    h6 = np.maximum(np.median(nn[:, 1:], axis=1), 1e-12)  # a first, rough length scale

    if length < max(1e-3 * diag, float(h6.min())):
        raise ValueError(
            "the section line is %.3g mm long, shorter than 0.1%% of the model (%.3g mm) or than one "
            "element (%.3g mm): too short to linearize" % (length, diag, float(h6.min()))
        )
    check_cancel()
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    slack = np.maximum(h6, d1)
    if np.any((pos < lo - slack[:, None]) | (pos > hi + slack[:, None])):
        raise ValueError(
            "line leaves the meshed region (outside the nodes) - check the section end points"
        )

    # triangulate only the nodes near the line; the reach covers half an element even when the
    # elements are much longer than they are wide (the line then runs between node columns)
    near = set()
    for lst in tree.query_ball_point(pos, r=4.0 * np.maximum(h6, d1)):
        near.update(lst)
    idx = np.fromiter(near, dtype=int)
    if len(idx) < d + 1:
        raise ValueError("fewer than %d nodes lie near the section line" % (d + 1))
    sub = pts[idx]
    try:
        tri = Delaunay(sub)
    except Exception as e:  # scipy raises QhullError for collinear / coplanar node sets
        raise ValueError(
            "the nodes cannot be triangulated (all on one line or plane?): %s"
            % str(e).splitlines()[0]
        ) from None
    simp = tri.find_simplex(pos)  # exact containment first ...
    if np.any(simp < 0):  # ... then the snapping tolerance, only for the samples that need it
        loose = tri.find_simplex(pos[simp < 0], tol=snap_fraction)
        simp[simp < 0] = loose
    if np.any(simp < 0):
        bad = [i for i in range(n) if simp[i] < 0]
        raise ValueError(
            "line leaves the meshed region: %d sample(s), the first at distance %.4g mm along the line, "
            "lie outside the mesh by more than the snapping tolerance (%g of an element)"
            % (len(bad), s[bad[0]], snap_fraction)
        )

    # gap detection: compare the longest edge of each sample's simplex with the simplices around it
    P = sub[tri.simplices]  # (m, d+1, d)
    longest = np.zeros(len(P))
    for i in range(d + 1):
        for j in range(i + 1, d + 1):
            longest = np.maximum(longest, np.linalg.norm(P[:, i] - P[:, j], axis=1))
    cent = P.mean(axis=1)
    ctree = cKDTree(cent)
    in_gap = np.zeros(n, dtype=bool)
    for i in range(n):
        if i % 64 == 0:
            check_cancel()
        ls = longest[simp[i]]
        around = ctree.query_ball_point(cent[simp[i]], r=3.0 * ls)
        near_sizes = longest[around]
        # a void is bridged by a few huge simplices among many small ones; a step in element size
        # (fine -> coarse) also has big simplices, but plenty of them around: those are not gaps
        in_gap[i] = (
            ls > GAP_RATIO * float(np.median(near_sizes))
            and float(np.mean(near_sizes >= 0.5 * ls)) < GAP_SIMILAR_FRACTION
        )
    gaps = []
    run = None
    for i, bad in enumerate(in_gap):
        if bad and run is None:
            run = i
        if (not bad or i == n - 1) and run is not None:
            gaps.append((s[max(run - 1, 0)], s[min(i, n - 1)]))
            run = None
    if gaps and not allow_gaps:
        big = max(gaps, key=lambda g: g[1] - g[0])
        mid = 0.5 * (big[0] + big[1]) / length
        diag_info = {
            "largest_span_mm": [big[0], big[1]],
            "largest_span_length_mm": big[1] - big[0],
            "location_mm": [
                float(x) for x in np.asarray(a) + (np.asarray(b) - np.asarray(a)) * mid
            ],
            "spans_mm": [[g[0], g[1]] for g in gaps[:10]],
            "typical_element_size_mm": float(np.median(longest)),
        }
        raise GapError(
            "the section line crosses a gap with no nodes (a hole or notch?) at distance %s along "
            "the line (largest %.4g mm); interpolating across it would invent values. Move the "
            "line or pass allow_gaps=true to accept it"
            % (", ".join("%.4g to %.4g mm" % g for g in gaps[:5]), big[1] - big[0]),
            diag_info,
        )

    T = tri.transform[simp]
    if np.any(~np.isfinite(T)):
        raise ValueError("the triangulation near the line is degenerate")
    bary = np.einsum("ijk,ik->ij", T[:, :d, :], pos - T[:, d, :])
    bary = np.concatenate([bary, 1.0 - bary.sum(axis=1, keepdims=True)], axis=1)
    v = [float(x) for x in np.sum(bary * vals[idx][tri.simplices[simp]], axis=1)]
    if any(not math.isfinite(x) for x in v):
        raise ValueError(
            "line leaves the meshed region (NaN samples) - check the section end points"
        )
    adjustments = [
        {"sample": i, "distance_mm": s[i], "outside_by_simplex_fraction": float(-bary[i].min())}
        for i in range(n)
        if bary[i].min() < -1e-9
    ]
    info = {
        "merged_nodes": int(merged),
        "endpoint_adjustments": adjustments,
        "interface_conflicts": conflicts,
    }
    return s, v, gaps, info


def linearize(s: Sequence[float], v: Sequence[float]) -> Dict[str, float]:
    """Membrane, bending and peak of a through-thickness stress profile (ASME definitions).

    The profile v(s) is taken as piecewise linear between the samples and integrated exactly:
      membrane  = (1/t) * integral v ds
      bending   = value of the linear bending stress at s = 0 (it is minus that at s = t), with
                  M = integral v (t/2 - s) ds and bending = 6 M / t^2
      peak(s)   = v(s) - membrane - bending * (1 - 2 s / t)      (total minus membrane + bending)
    `peak` is the peak(s) of largest magnitude, kept with its sign.
    """
    if len(s) != len(v) or len(s) < 2:
        raise ValueError("need at least two samples along the section")
    s0 = s[0]
    x = [si - s0 for si in s]
    t = x[-1]
    if not t > 0 or any(x[i + 1] <= x[i] for i in range(len(x) - 1)):
        raise ValueError("sample distances must increase along a section of positive length")
    if any(not math.isfinite(vi) for vi in v):
        raise ValueError("the stress values contain NaN or infinity")
    mem_i = 0.0
    mom = 0.0
    for i in range(len(x) - 1):
        h = x[i + 1] - x[i]
        mem_i += 0.5 * h * (v[i] + v[i + 1])
        w0, w1 = t / 2 - x[i], t / 2 - x[i + 1]
        mom += h * (v[i] * (2 * w0 + w1) + v[i + 1] * (w0 + 2 * w1)) / 6.0
    mem = mem_i / t
    b0 = 6.0 * mom / (t * t)
    peaks = [v[i] - mem - b0 * (1.0 - 2.0 * x[i] / t) for i in range(len(x))]
    k = max(range(len(peaks)), key=lambda i: abs(peaks[i]))
    return {
        "membrane": mem,
        "bending": b0,
        "mem_plus_bend_start": mem + b0,
        "mem_plus_bend_end": mem - b0,
        "mem_plus_bend_max": max(abs(mem + b0), abs(mem - b0)),
        "peak": peaks[k],
        "peak_at_start": peaks[0],
        "peak_at_end": peaks[-1],
        "peak_location": x[k],
        "total_at_start": v[0],
        "total_at_end": v[-1],
        "section_length": t,
    }


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not isinstance(d, dict):
        raise ValueError("not an export_nodal_results file")
    xyz, vals = d.get("xyz_mm"), d.get("values")
    if not isinstance(xyz, list) or not isinstance(vals, list):
        raise ValueError(
            "the file has no 'xyz_mm' / 'values' lists (is it from export_nodal_results?)"
        )
    if len(xyz) != len(vals) or not xyz:
        raise ValueError("'xyz_mm' and 'values' differ in length or are empty")
    for p in xyz:
        if not isinstance(p, list) or len(p) != 3:
            raise ValueError("every entry of 'xyz_mm' must be [x, y, z]")
    return d


def linearize_file(
    path: str,
    a_mm,
    b_mm,
    plane: str = "xy",
    n: int = 81,
    *,
    slice_mm=None,
    slice_tol_mm: float = 0.01,
    allow_unsigned: bool = False,
    allow_gaps: bool = False,
    expect_component: str = "",
    snap_fraction: float = 0.25,
    accept_direction_mismatch: bool = False,
    cancel=None,
) -> Dict[str, object]:
    """Linearize the values in a file written by export_nodal_results along a section line.

    a_mm and b_mm are either (x, y, z) -- a 3D line through all nodes -- or two numbers in the
    chosen coordinate plane (xy, xz or yz). In the 2D form the data must lie in one plane, or
    slice_mm must give the coordinate normal to the plane (nodes within slice_tol_mm are used);
    otherwise layers at different depths would be mixed into one wrong membrane.
    """
    axes = {"xy": (0, 1, 2), "xz": (0, 2, 1), "yz": (1, 2, 0)}
    if plane not in axes:
        raise ValueError("plane must be xy, xz or yz")
    d = _load(path)
    if len(d["values"]) > MAX_NODES:
        raise ValueError(
            "the export has %d nodes; the limit is %d (MECHANICAL_MCP_MAX_NODES)"
            % (len(d["values"]), MAX_NODES)
        )
    name = str(d.get("result", ""))
    ori = str(d.get("orientation", "") or "")
    warnings = []
    info_flags: Dict[str, object] = {}
    if not (isinstance(snap_fraction, (int, float)) and 0 <= snap_fraction <= 1):
        raise ValueError("snap_fraction must be between 0 and 1")
    if expect_component:
        want = expect_component.strip().lower()
        if want not in ("x", "y", "z"):
            raise ValueError("expect_component must be x, y or z")
        if ori and ori.lower()[0] != want:
            raise ValueError(
                "the export holds the %s component of %r but expect_component is %s"
                % (ori, name, want.upper())
            )
        if not ori:
            info_flags["component_unverified"] = True
            warnings.append(
                "the export file does not record which component it holds, so expect_component=%s "
                "could not be checked" % want.upper()
            )
    signed = d.get("signed")
    if signed is None:
        signed = not _UNSIGNED_NAME.search(name)
    if not signed and not allow_unsigned:
        raise ValueError(
            "%r is an unsigned quantity (e.g. von Mises or stress intensity). Linearizing an "
            "absolute value is non-conservative: export a signed stress component (add_normal_stress) "
            "and linearize that, or pass allow_unsigned=true to override." % name
        )
    vals = [float(v) for v in d["values"]]
    if any(not math.isfinite(v) for v in vals):
        raise ValueError("the export contains NaN or infinite values")
    pts = [tuple(float(c) for c in p) for p in d["xyz_mm"]]
    info: Dict[str, object] = {"result": name, "signed": bool(signed)}

    if len(a_mm) == 3:
        nodes = pts
        info["line"] = "3D"
    else:
        i, j, k = axes[plane]
        ks = [p[k] for p in pts]
        lo, hi = min(ks), max(ks)
        if hi - lo > slice_tol_mm:
            if slice_mm is None:
                raise ValueError(
                    "the data spans %s from %.6g to %.6g mm, so a 2D section would mix layers; "
                    "pass slice_mm (the %s coordinate of the plane to cut) or give 3D end points"
                    % ("xyz"[k], lo, hi, "xyz"[k])
                )
            keep = [q for q in range(len(pts)) if abs(pts[q][k] - slice_mm) <= slice_tol_mm]
            if len(keep) < 3:
                raise ValueError(
                    "only %d nodes lie within %g mm of %s = %g; raise slice_tol_mm or check slice_mm"
                    % (len(keep), slice_tol_mm, "xyz"[k], slice_mm)
                )
            pts = [pts[q] for q in keep]
            vals = [vals[q] for q in keep]
            info["slice"] = {"axis": "xyz"[k], "value_mm": slice_mm, "nodes": len(keep)}
        nodes = [(p[i], p[j]) for p in pts]
        info["line"] = "2D in plane " + plane
    # direction of the line against the exported component
    if len(a_mm) == 3:
        vec = [b_mm[q] - a_mm[q] for q in range(3)]
    else:
        vec = [0.0, 0.0, 0.0]
        vec[axes[plane][0]] = b_mm[0] - a_mm[0]
        vec[axes[plane][1]] = b_mm[1] - a_mm[1]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    line_axis = max(range(3), key=lambda q: abs(vec[q]))
    aligned = abs(vec[line_axis]) / norm >= 0.9
    if ori and ori[0].lower() in "xyz" and aligned and ori[0].lower() != "xyz"[line_axis]:
        msg = (
            "the line runs along %s but the export holds the %s component; linearizing a component "
            "that is not the one along (or across) the line you meant gives a meaningless split. Pass "
            "accept_direction_mismatch=true if this is intended" % ("XYZ"[line_axis], ori)
        )
        if not accept_direction_mismatch:
            raise ValueError(msg)
        warnings.append(msg)
        info_flags["direction_mismatch"] = True
    s, v, gaps, sinfo = sample_line(
        nodes,
        vals,
        tuple(a_mm),
        tuple(b_mm),
        n,
        allow_gaps=allow_gaps,
        snap_fraction=snap_fraction,
        cancel=cancel,
    )
    out = linearize(s, v)
    out["section_length_mm"] = out.pop("section_length")
    out["samples"] = n
    out["interpolated_across_gap"] = bool(gaps)
    if gaps:
        out["gap_spans_mm"] = [[g[0], g[1]] for g in gaps]
    out["component"] = ori or "not recorded in the file"
    out["linearized_quantity"] = (
        "the single exported scalar only (%s); other components are not combined" % name
    )
    claimed = d.get("value_unit", "unknown")
    verified = d.get("value_unit_verified")
    out["value_unit_claimed"] = claimed
    out["value_unit_verified"] = bool(verified) if verified is not None else None
    out["value_unit"] = (
        claimed if verified is True else ("unverified" if verified is False else "unknown")
    )
    if verified is not True:
        warnings.append(
            "the unit of the exported values was not cross-checked against the result's own maximum"
        )
    out["merged_nodes"] = sinfo["merged_nodes"]
    if sinfo["interface_conflicts"]:
        out["discontinuity_at_interface"] = True
        out["interface_values"] = sinfo["interface_conflicts"]
        warnings.append(
            "coincident interface nodes carry different values (a jump across the interface): they "
            "were averaged, so a line crossing the interface sees the mean, not either side"
        )
    out["endpoint_adjustments"] = sinfo["endpoint_adjustments"]
    out["warnings"] = warnings
    out.update(info_flags)
    out["bending_convention"] = (
        "bending is the bending stress at the start point; the end point has the opposite sign"
    )
    out.update(info)
    return out
