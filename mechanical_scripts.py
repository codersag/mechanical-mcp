"""Pure builders for the IronPython snippets run inside Mechanical, plus argument validation.

Nothing here imports mcp or ansys, so the generated code can be unit tested without a
Mechanical session. mechanical_mcp_server.py wraps these in tools.

Conventions for generated scripts:
  * every user-supplied string is embedded with lit() -- an ASCII-only u"..." literal (non-ASCII
    becomes \\uXXXX), so the script text never depends on a source encoding;
  * every script prints exactly one JSON object with an "ok" key;
  * every script that creates an object removes it again if anything after the creation fails;
  * scripts define what they need themselves and never rely on names left behind by an earlier
    script (the server also runs each script in a private copy of the globals);
  * scripts stay valid in both IronPython 2.7 and CPython so the unit tests can run them against
    a fake object model.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import List, Optional

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

#: Longest string argument accepted (names, paths).
MAX_STR = 10_000

# Control characters (other than tab, newline, carriage return) and the Unicode line separators
# have no business in a tool argument.
BAD_CHARS = frozenset(
    [chr(c) for c in (*range(0, 9), 11, 12, *range(14, 32), *range(127, 160))]
    + [chr(0x2028), chr(0x2029)]
)

_ASCII_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_ASCII_INT = re.compile(r"[0-9]+")

#: Largest geometry id accepted (a signed 32-bit int in Mechanical).
MAX_ID = 2_147_483_647
MAX_IDS = 100_000


def _bad_codepoint(c: str) -> bool:
    o = ord(c)
    # lone surrogates (U+D800-DFFF: a name containing one poisons the model -- Mechanical stores it
    # and then fails to encode it), noncharacters
    return 0xD800 <= o <= 0xDFFF or 0xFDD0 <= o <= 0xFDEF or (o & 0xFFFE) == 0xFFFE


def check_text(name: str, s) -> str:
    """Return s if it is a str that is safe to embed in a script; raise ValueError otherwise."""
    if not isinstance(s, str):
        raise ValueError("%s must be a string" % name)
    if len(s) > MAX_STR:
        raise ValueError("%s may not exceed %d characters" % (name, MAX_STR))
    bad = next((c for c in s if c in BAD_CHARS), None)
    if bad is not None:
        raise ValueError("%s contains control character U+%04X" % (name, ord(bad)))
    bad = next((c for c in s if _bad_codepoint(c)), None)
    if bad is not None:
        raise ValueError(
            "%s contains the invalid character U+%04X (unpaired surrogate or noncharacter)"
            % (name, ord(bad))
        )
    return s


def esc(s: str) -> str:
    """Escape a string for the inside of an IronPython double-quoted unicode literal.

    Quotes, backslashes and newlines are escaped, and every non-ASCII character becomes a
    \\uXXXX escape (a surrogate pair above U+FFFF), so the result is plain ASCII: the script
    can be written and read in any encoding and a name such as 'caf\\u00e9_\\u00d8' arrives
    intact. Raises ValueError for control characters.
    """
    check_text("argument", s)
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= o < 127:
            out.append(ch)
        elif o <= 0xFFFF:
            out.append("\\u%04x" % o)
        else:
            o -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
    return "".join(out)


def lit(s: str) -> str:
    """A complete ASCII-only unicode string literal: u"..."."""
    return 'u"' + esc(s) + '"'


def num(
    name: str,
    v,
    *,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
    positive: bool = False,
    limit: float = 1e12,
) -> float:
    """A finite float within [lo, hi] (and > 0 if positive), else ValueError.

    bool, nan, inf and absurdly large values are refused: they would otherwise be spliced into
    generated code ('nan' is not a name in IronPython) or accepted silently by Mechanical.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("%s must be a number" % name)
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("%s must be a finite number" % name)
    if positive and f <= 0:
        raise ValueError("%s must be greater than zero" % name)
    if lo is not None and f < lo:
        raise ValueError("%s must be at least %g" % (name, lo))
    if hi is not None and f > hi:
        raise ValueError("%s must be at most %g" % (name, hi))
    if abs(f) > limit:
        raise ValueError("%s is unreasonably large (|value| > %g)" % (name, limit))
    return f


def integer(name: str, v, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("%s must be an integer" % name)
    if v < lo or v > hi:
        raise ValueError("%s must be between %d and %d" % (name, lo, hi))
    return int(v)


class AnalysisRef(int):
    """An analysis index that remembers how the caller chose it (explicitly or by default), an
    optional analysis name, an expected analysis type, and whether the tool changes the model."""

    explicit = True
    name = ""
    expect = ""
    mutating = False

    def __new__(cls, value, explicit=True, name="", expect="", mutating=False):
        obj = int.__new__(cls, value)
        obj.explicit, obj.name, obj.expect, obj.mutating = explicit, name, expect, mutating
        return obj


def index(v, *, name: str = "", expect: str = "", mutating: bool = False) -> int:
    """analysis_index: a small non-negative integer (None = not given, use 0)."""
    if isinstance(v, AnalysisRef) and not (name or expect or mutating):
        return v
    check_text("analysis_name", name)
    check_text("expect_analysis_type", expect)
    value = 0 if v is None else integer("analysis_index", v, 0, 999)
    return AnalysisRef(value, v is not None, name.strip(), expect.strip(), mutating)


def fnum(x: float) -> str:
    """A float as source text (always a plain decimal literal; the value must already be finite)."""
    f = float(x)
    if not math.isfinite(f):
        raise ValueError("non-finite number")
    return repr(f)


def parse_ids(text: str) -> List[int]:
    """'12, 15 18' -> [12, 15, 18]. Ids are positive integers written with ASCII digits."""
    check_text("entity_ids", text)
    parts = [p for p in text.replace(",", " ").split() if p]
    if not parts:
        raise ValueError("entity_ids is empty")
    if len(parts) > MAX_IDS:
        raise ValueError("entity_ids lists more than %d ids" % MAX_IDS)
    ids: List[int] = []
    seen = set()
    for p in parts:
        # int() would accept '1_0', ' 10 ' and non-ASCII digits; none of those is an id.
        if not _ASCII_INT.fullmatch(p):
            raise ValueError("entity id %r is not a positive integer" % p)
        v = int(p)
        if v <= 0:
            raise ValueError("entity ids must be positive, got %d" % v)
        if v > MAX_ID:
            raise ValueError("entity id %d is out of range" % v)
        if v not in seen:
            seen.add(v)
            ids.append(v)
    return ids


def _box_value(p: str, axis: str) -> Optional[float]:
    if p == "" or p.lower() == "none":
        return None
    if not _ASCII_NUMBER.fullmatch(p):
        raise ValueError("box_mm: %r is not a number" % p)
    v = float(p)
    if not math.isfinite(v) or abs(v) > 1e9:
        raise ValueError("box_mm: %r is out of range" % p)
    return v


def parse_box_mm(text: str) -> List[Optional[float]]:
    """'xmin,xmax,ymin,ymax,zmin,zmax' in mm; a blank or 'none' entry leaves that side open."""
    check_text("box_mm", text)
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 6:
        raise ValueError("box_mm needs 6 comma-separated values: xmin,xmax,ymin,ymax,zmin,zmax")
    out = [_box_value(p, "XXYYZZ"[i]) for i, p in enumerate(parts)]
    for i in range(3):
        lo, hi = out[2 * i], out[2 * i + 1]
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("box_mm: minimum is greater than maximum on axis %s" % "XYZ"[i])
    return out


def indent(text: str, n: int = 4) -> str:
    pad = " " * n
    return "".join((pad + ln if ln.strip() else ln) for ln in text.splitlines(True))


# ---------------------------------------------------------------------------
# Shared script fragments
# ---------------------------------------------------------------------------

# Look up a named selection by name into _ns (None when absent).
NS_LOOKUP = (
    "_ns = None\n"
    "for _item in ExtAPI.DataModel.Project.Model.GetChildren(DataModelObjectCategory.NamedSelection, True):\n"
    "    if str(_item.Name) == _NS_NAME:\n"
    "        _ns = _item\n"
    "        break\n"
)

# After NS_LOOKUP: an empty named selection is as useless as a missing one, and a load scoped to
# it would be created with no effect.
_NS_NONEMPTY = (
    '_ns_err = u"Named selection not found: " + _NS_NAME\n'
    "if _ns is not None:\n"
    "    try:\n"
    "        _cnt = len(_ns.Location.Ids)\n"
    "    except Exception:\n"
    "        _cnt = 0\n"
    "    if _cnt == 0:\n"
    '        _ns_err = u"Named selection is empty (it holds no geometry entities): " + _NS_NAME\n'
    "        _ns = None\n"
)

_ENTITY_ATTR = {"face": "Faces", "edge": "Edges", "vertex": "Vertices"}

#: Model length units (GeoData.Unit) -> millimetres / metres per unit.
TO_MM = {"m": 1000.0, "mm": 1.0, "cm": 10.0, "um": 0.001, "in": 25.4, "ft": 304.8}
_TO_MM_SRC = "{'m': 1000.0, 'mm': 1.0, 'cm': 10.0, 'um': 0.001, 'in': 25.4, 'ft': 304.8}"


# The analysis at index _IDX, with a clear error for an index the model does not have.
def pick_analysis(idx: int) -> str:
    """Select the analysis, check it is the one intended, and print an @@ANALYSIS marker line that
    the server turns into `analysis: {index, name, type}` in the response.

    A tool that changes the model, called without an explicit analysis_index (or analysis_name) on
    a model with several analyses, is refused with the list: index 0 is often not the analysis
    the caller means (set MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS=0 to switch this off)."""
    name = getattr(idx, "name", "")
    expect = getattr(idx, "expect", "")
    explicit = getattr(idx, "explicit", True)
    mutating = getattr(idx, "mutating", False)
    require = os.environ.get("MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS", "1").strip() != "0"
    return (
        "_IDX = %d\n"
        "_model = ExtAPI.DataModel.Project.Model\n"
        "_n_an = len(_model.Analyses)\n"
        "_AN_NAME = %s\n"
        "_EXPECT = %s\n"
        "def _an_list():\n"
        "    return u'; '.join([u'[' + str(_i) + u'] ' + str(_a.Name) + u' (' + str(_a.AnalysisType) + u')' "
        "for _i, _a in enumerate(_model.Analyses)])\n"
        "if _AN_NAME:\n"
        "    _hits = [_i for _i, _a in enumerate(_model.Analyses) if str(_a.Name) == _AN_NAME]\n"
        "    if len(_hits) != 1:\n"
        "        raise Exception(u'analysis_name ' + _AN_NAME + u' matches ' + str(len(_hits)) + u' analyses; the model has: ' + _an_list())\n"
        "    _IDX = _hits[0]\n"
        "elif %s and _n_an > 1:\n"
        "    raise Exception(u'analysis_index was not given and the model has ' + str(_n_an) + u' analyses, so the one "
        "meant is ambiguous: ' + _an_list() + u'. Pass analysis_index (or analysis_name).')\n"
        "if _IDX >= _n_an:\n"
        "    raise Exception('analysis_index %%d is out of range: the model has %%d analyses' %% (_IDX, _n_an))\n"
        "_analysis = _model.Analyses[_IDX]\n"
        "if _EXPECT and str(_analysis.AnalysisType) != _EXPECT:\n"
        "    raise Exception(u'analysis ' + str(_IDX) + u' (' + str(_analysis.Name) + u') is a ' + str(_analysis.AnalysisType) "
        "+ u' analysis, not the expected ' + _EXPECT + u'. The model has: ' + _an_list())\n"
        "_an_info = {'index': _IDX, 'name': str(_analysis.Name), 'type': str(_analysis.AnalysisType)}\n"
        "print('@@ANALYSIS ' + json.dumps(_an_info))\n"
        % (
            int(idx),
            lit(name),
            lit(expect),
            "True" if (mutating and not explicit and not name and require) else "False",
        )
    )


def locator_for(named_selection: str, entity_ids: str, box_mm: str) -> int:
    """How many of the three location arguments were given (blank = not given)."""
    return sum(bool(s.strip()) for s in (named_selection, entity_ids, box_mm))


def location_snippet(
    named_selection: str = "",
    entity_ids: str = "",
    box_mm: str = "",
    entity_type: str = "face",
    body_name: str = "",
    fully_inside: bool = False,
) -> str:
    """IronPython that leaves a load location in _ns (None on failure, reason in _ns_err).

    Exactly one of named_selection, entity_ids or box_mm picks the geometry. _ns is either a
    named selection object or a SelectionInfo; both are valid for `Location = _ns`. A named
    selection that is missing or empty, ids that do not exist and a box that matches nothing
    all leave _ns as None. _ns_warn is a list of warning texts (for example a box that cuts
    through entities: a box selects entities by their CENTROID unless fully_inside is set).
    """
    for nm, val in (
        ("named_selection", named_selection),
        ("entity_ids", entity_ids),
        ("box_mm", box_mm),
        ("body_name", body_name),
    ):
        check_text(nm, val)
    if locator_for(named_selection, entity_ids, box_mm) != 1:
        raise ValueError("give exactly one of named_selection, entity_ids, box_mm")
    if entity_type not in _ENTITY_ATTR:
        raise ValueError("entity_type must be one of: " + ", ".join(sorted(_ENTITY_ATTR)))
    if not isinstance(fully_inside, bool):
        raise ValueError("fully_inside must be true or false")
    if fully_inside and not box_mm.strip():
        raise ValueError("fully_inside only applies to box_mm")

    if named_selection.strip():
        return "_ns_warn = []\n_NS_NAME = " + lit(named_selection) + "\n" + NS_LOOKUP + _NS_NONEMPTY

    walk_head = (
        "_BODY = " + lit(body_name) + "\n"
        "_ids = []\n"
        "for _asm in ExtAPI.DataModel.GeoData.Assemblies:\n"
        "    for _part in _asm.Parts:\n"
        "        for _body in _part.Bodies:\n"
        "            if _BODY and str(_body.Name) != _BODY:\n"
        "                continue\n"
    )

    if entity_ids.strip():
        ids = parse_ids(entity_ids)
        return (
            "_ns = None\n"
            "_ns_warn = []\n"
            "_REQ = " + repr(ids) + "\n"
            "_REQSET = set(_REQ)\n"
            "_found = set()\n"
            + walk_head
            + "            for _attr in ('Faces', 'Edges', 'Vertices'):\n"
            "                for _e in getattr(_body, _attr):\n"
            "                    if int(_e.Id) in _REQSET:\n"
            "                        _found.add(int(_e.Id))\n"
            "_missing = [_i for _i in _REQ if _i not in _found]\n"
            "if _missing:\n"
            "    _ns_err = u'No geometry entities with these ids' + (u' (on that body)' if _BODY else u'') "
            "+ u': ' + u', '.join([str(_i) for _i in _missing[:20]])\n"
            "else:\n"
            "    _ns_err = u''\n"
            "    _sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
            "    _sel.Ids = _REQ\n"
            "    _ns = _sel\n"
        )

    box = parse_box_mm(box_mm)
    return (
        "_ns = None\n"
        "_ns_warn = []\n"
        '_ns_err = u"No geometry matched the box"\n'
        "_BOX = " + repr(box) + "\n"
        '_ENT = "' + _ENTITY_ATTR[entity_type] + '"\n'
        "_FULL = " + ("True" if fully_inside else "False") + "\n"
        "_TOL = 1e-3\n"
        "_TO_MM = " + _TO_MM_SRC + "\n"
        "_unit = str(ExtAPI.DataModel.GeoData.Unit)\n"
        "if _unit not in _TO_MM:\n"
        "    raise Exception('unsupported geometry length unit: ' + _unit)\n"
        "_K = _TO_MM[_unit]\n"
        "def _inbox(_pt):\n"
        "    for _i in range(3):\n"
        "        _lo = _BOX[2 * _i]\n"
        "        _hi = _BOX[2 * _i + 1]\n"
        "        if _lo is not None and _pt[_i] < _lo - _TOL:\n"
        "            return False\n"
        "        if _hi is not None and _pt[_i] > _hi + _TOL:\n"
        "            return False\n"
        "    return True\n"
        "_cut = 0\n" + walk_head + "            for _e in getattr(_body, _ENT):\n"
        "                if hasattr(_e, 'Centroid'):\n"
        "                    _c = _e.Centroid\n"
        "                else:\n"
        "                    _c = [_e.X, _e.Y, _e.Z]\n"
        "                if not _inbox([_c[0] * _K, _c[1] * _K, _c[2] * _K]):\n"
        "                    continue\n"
        "                _whole = True\n"
        "                if hasattr(_e, 'GetBoundingBox'):\n"
        "                    try:\n"
        "                        _bb = _e.GetBoundingBox()\n"
        "                        _whole = _inbox([_bb[0] * _K, _bb[1] * _K, _bb[2] * _K]) and "
        "_inbox([_bb[3] * _K, _bb[4] * _K, _bb[5] * _K])\n"
        "                    except Exception:\n"
        "                        _whole = True\n"
        "                if _FULL and not _whole:\n"
        "                    continue\n"
        "                if not _whole:\n"
        "                    _cut += 1\n"
        "                _ids.append(int(_e.Id))\n"
        "if _cut:\n"
        "    _ns_warn.append(str(_cut) + u' of ' + str(len(_ids)) + u' selected entities extend outside the box: "
        "a box selects by CENTROID (pass fully_inside=true to require whole entities)')\n"
        "if _ids:\n"
        "    _sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "    _sel.Ids = _ids\n"
        "    _ns = _sel\n"
    )


# ---------------------------------------------------------------------------
# Generic builders: create an object, configure it, undo everything on failure
# ---------------------------------------------------------------------------

_FAIL_BLOCK = (
    "except Exception as _e:\n"
    "    _msg = str(_e)\n"
    "    _rb = False\n"
    "    if _obj is not None and _fresh:\n"
    "        try:\n"
    "            _obj.Delete()\n"
    "            _rb = True\n"
    "        except Exception:\n"
    "            pass\n"
    "    _info = {'ok': False, 'error': _msg, 'created': False, 'rolled_back': _rb}\n"
    "    if _obj is not None and _fresh and not _rb:\n"
    "        _info['error'] = _msg + u' (the partly created object could not be removed; delete it in the tree)'\n"
    "        _info['created'] = True\n"
)


def create_block(create_expr: str, config: str, info: str) -> str:
    """try: create + configure + read back; except: delete the new object and report.

    create_expr is evaluated to make the object (bound to _obj). config is a block of statements
    that may use _obj. info is a Python dict expression (as text) evaluated after configuration,
    still inside the try, so a failed read-back also rolls the object back. Prints one JSON line.
    """
    return (
        "_obj = None\n"
        "_fresh = True\n"
        "_info = None\n"
        "try:\n"
        "    _obj = "
        + create_expr
        + "\n"
        + indent(config)
        + "    _info = "
        + info
        + "\n"
        + _FAIL_BLOCK
        + "print(json.dumps(_info))\n"
    )


#: Object states that mean "not usable yet": a load whose selection Mechanical did not accept
#: (a body selection on a face load, say) stays UnderDefined and has no Location.
BAD_STATES = ("UnderDefined", "Undefined", "Error", "Failed")

# What the selection holds: body / face / edge / vertex, from the first entity id.
SCOPE_TYPE_DEFS = (
    "def _scope_type(_s):\n"
    "    try:\n"
    "        _ids = list(_s.Ids)\n"
    "    except Exception:\n"
    "        try:\n"
    "            _ids = list(_s.Location.Ids)\n"
    "        except Exception:\n"
    "            return None\n"
    "    if not _ids:\n"
    "        return None\n"
    "    try:\n"
    "        _n = type(ExtAPI.DataModel.GeoData.GeoEntityById(int(_ids[0]))).__name__.lower()\n"
    "    except Exception:\n"
    "        return None\n"
    "    for _k in ('body', 'face', 'edge', 'vertex'):\n"
    "        if _k in _n:\n"
    "            return _k\n"
    "    return _n\n"
)


def load_script(
    idx: int,
    adder: str,
    label: str,
    location: str,
    config: str = "",
    host: str = "_analysis",
) -> str:
    """Add a load or support to the analysis, scoped by a location_snippet().

    After the scope and the values are set the load must have a Location and a usable
    ObjectState: Mechanical silently refuses, say, a body-type named selection for a face load
    (Location stays None, the object stays UnderDefined). That is a failure with rollback, not a
    success with scope_entities null."""
    info = (
        "{'ok': True, 'created': True, 'type': " + lit(label) + ", 'name': str(_obj.Name), "
        "'scope_entities': _scope, 'scope_type': _stype, 'object_state': _state, 'warnings': _ns_warn}"
    )
    scope_check = (
        "_stype = _scope_type(_ns)\n"
        "_obj.Location = _ns\n"
        "try:\n"
        "    _scope = len(_obj.Location.Ids)\n"
        "except Exception:\n"
        "    _scope = None\n"
        "if _scope is not None and _scope == 0:\n"
        "    raise Exception('the load has an empty scope')\n"
    )
    after = (
        "_loc_now = None\n"
        "try:\n"
        "    _loc_now = _obj.Location\n"
        "except Exception:\n"
        "    pass\n"
        "_state = str(_obj.ObjectState)\n"
        "if _loc_now is None:\n"
        "    raise Exception(u'Mechanical did not accept the selection as the scope of this ' + "
        + lit(label)
        + " + u' (the selection holds ' + str(_stype) + u' entities; this load needs face, edge or vertex scoping "
        "unless Mechanical documents otherwise)')\n"
        "if _state in " + repr(BAD_STATES) + ":\n"
        "    raise Exception(u'the load is ' + _state + u' after scoping and setting its values (selection type ' + "
        "str(_stype) + u')')\n"
        "if _scope is None:\n"
        "    _scope = _count_ids(_loc_now)\n"
    )
    return (
        "import json\n"
        + (_SETQ if "_setq(" in config else "")
        + SCOPE_TYPE_DEFS
        + "def _count_ids(_x):\n"
        "    try:\n"
        "        return len(_x.Ids)\n"
        "    except Exception:\n"
        "        return None\n" + pick_analysis(idx) + location + "if _ns is None:\n"
        "    print(json.dumps({'ok': False, 'error': _ns_err, 'created': False}))\n"
        "else:\n"
        + indent(create_block(host + "." + adder + "()", scope_check + config + after, info))
    )


# A component that starts out "Free" (displacements) has no values yet, and SetDiscreteValue on it
# raises "Object reference not set..." (seen live on 2026 R1); assigning DiscreteValues works and
# makes the field settable. The value Mechanical holds is read back and compared.
_SETQ = (
    "def _setq(_field, _val, _unit):\n"
    "    _q = Quantity(_val, _unit)\n"
    "    try:\n"
    "        _field.Output.SetDiscreteValue(0, _q)\n"
    "    except Exception:\n"
    "        _field.Output.DiscreteValues = [_q]\n"
    "    _dv = _field.Output.DiscreteValues\n"
    "    _got = float(_dv[len(_dv) - 1].ConvertUnit(_unit).Value)\n"
    "    if abs(_got - _val) > 1e-6 * max(1.0, abs(_val)):\n"
    "        raise Exception('Mechanical holds ' + str(_got) + ' ' + _unit + ' instead of ' + str(_val))\n"
)


def _vec_config(kind: str, values, unit: str, scale: float = 1.0) -> str:
    """_setq lines for X/Y/Z translations/forces (kind 'Component': XComponent) or rotations
    (kind 'Rotation': RotationX), given as (axis, value-or-None) pairs."""
    out = ""
    for axis, val in values:
        if val is None:
            continue
        attr = axis + "Component" if kind == "Component" else "Rotation" + axis
        out += "_setq(_obj.%s, %s, %s)\n" % (attr, fnum(val * scale), '"' + unit + '"')
    return out


def fixed_support_script(idx: int, location: str) -> str:
    return load_script(idx, "AddFixedSupport", "Fixed Support", location)


def frictionless_support_script(idx: int, location: str) -> str:
    return load_script(idx, "AddFrictionlessSupport", "Frictionless Support", location)


def force_script(idx: int, location: str, fx: float, fy: float, fz: float) -> str:
    cfg = "_obj.DefineBy = LoadDefineBy.Components\n" + _vec_config(
        "Component", [("X", fx), ("Y", fy), ("Z", fz)], "N"
    )
    return load_script(idx, "AddForce", "Force", location, cfg)


def pressure_script(idx: int, location: str, magnitude_pa: float) -> str:
    cfg = '_setq(_obj.Magnitude, %s, "Pa")\n' % fnum(magnitude_pa)
    return load_script(idx, "AddPressure", "Pressure", location, cfg)


def displacement_script(idx: int, location: str, x_mm, y_mm, z_mm) -> str:
    cfg = "_obj.DefineBy = LoadDefineBy.Components\n" + _vec_config(
        "Component", [("X", x_mm), ("Y", y_mm), ("Z", z_mm)], "m", 0.001
    )
    return load_script(idx, "AddDisplacement", "Displacement", location, cfg)


def remote_displacement_script(idx, location, x_mm, y_mm, z_mm, rx, ry, rz) -> str:
    cfg = _vec_config("Component", [("X", x_mm), ("Y", y_mm), ("Z", z_mm)], "m", 0.001)
    cfg += _vec_config("Rotation", [("X", rx), ("Y", ry), ("Z", rz)], "deg")
    return load_script(idx, "AddRemoteDisplacement", "Remote Displacement", location, cfg)


def remote_force_script(idx: int, location: str, fx: float, fy: float, fz: float) -> str:
    cfg = "_obj.DefineBy = LoadDefineBy.Components\n" + _vec_config(
        "Component", [("X", fx), ("Y", fy), ("Z", fz)], "N"
    )
    return load_script(idx, "AddRemoteForce", "Remote Force", location, cfg)


def moment_script(idx: int, location: str, mx: float, my: float, mz: float) -> str:
    cfg = "_obj.DefineBy = LoadDefineBy.Components\n" + _vec_config(
        "Component", [("X", mx), ("Y", my), ("Z", mz)], "N m"
    )
    return load_script(idx, "AddMoment", "Moment", location, cfg)


#: Standard Earth Gravity in Mechanical only takes a signed axis, not an arbitrary vector.
GRAVITY_AXES = {
    (1, 0, 0): "PositiveXAxis",
    (-1, 0, 0): "NegativeXAxis",
    (0, 1, 0): "PositiveYAxis",
    (0, -1, 0): "NegativeYAxis",
    (0, 0, 1): "PositiveZAxis",
    (0, 0, -1): "NegativeZAxis",
}


def gravity_direction(x: float, y: float, z: float):
    """Normalise a direction vector and map it to a signed axis name of Standard Earth Gravity.

    Only the direction matters: the magnitude is always 1 g (9.80665 m/s^2), so (0,-5,0) and
    (0,-1,0) are the same. Raises ValueError for a zero vector and for a direction that is not
    along +-X, +-Y or +-Z, which Mechanical's Standard Earth Gravity cannot represent.
    Returns (axis_enum_name, unit_vector).
    """
    vals = [num("x_component", x), num("y_component", y), num("z_component", z)]
    mag = math.sqrt(sum(v * v for v in vals))
    if mag < 1e-12:
        raise ValueError("the gravity direction vector must not be zero")
    unit = [v / mag for v in vals]
    key = tuple(int(round(u)) for u in unit)
    if key not in GRAVITY_AXES or max(abs(a - b) for a, b in zip(unit, key)) > 1e-9:
        raise ValueError(
            "Standard Earth Gravity only supports a direction along +X, -X, +Y, -Y, +Z or -Z "
            "(got normalised direction %s); Mechanical applies exactly 1 g along one axis"
            % ", ".join("%.4g" % u for u in unit)
        )
    return GRAVITY_AXES[key], unit


def gravity_script(idx: int, axis_enum: str) -> str:
    cfg = (
        "_obj.Direction = Ansys.Mechanical.DataModel.Enums.GravityOrientationType."
        + axis_enum
        + "\n"
    )
    # Components of the gravity vector as Mechanical reports them (read-only, derived from Direction).
    info = (
        "{'ok': True, 'created': True, 'type': 'Standard Earth Gravity', 'name': str(_obj.Name), "
        "'direction': str(_obj.Direction), 'vector': [str(_obj.XComponent), str(_obj.YComponent), "
        "str(_obj.ZComponent)]}"
    )
    return (
        "import json\n"
        + pick_analysis(idx)
        + create_block("_analysis.AddEarthGravity()", cfg, info)
    )


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------

_NOT_SOLVED = (
    "not solved yet: the result object was created but has no values until the analysis is solved"
)


# Finds an existing sibling that is the same kind of object with the same scope and settings, so
# asking twice does not create "Equivalent Stress 2" (double counted results, duplicate contacts).
_DEDUP_DEFS = (
    "_SCOPE_ATTRS = ('Location', 'GeometryLocation', 'SourceLocation', 'TargetLocation')\n"
    "def _scope_repr(_o):\n"
    "    _out = []\n"
    "    for _a in _SCOPE_ATTRS:\n"
    "        try:\n"
    "            _L = getattr(_o, _a)\n"
    "        except Exception:\n"
    "            continue\n"
    "        if _L is None:\n"
    "            continue\n"
    "        try:\n"
    "            _out.append((_a, 'ns', int(_L.ObjectId)))\n"
    "            continue\n"
    "        except Exception:\n"
    "            pass\n"
    "        try:\n"
    "            _out.append((_a, 'ids', tuple(sorted([int(_i) for _i in _L.Ids]))))\n"
    "        except Exception:\n"
    "            _out.append((_a, 'other', str(_L)))\n"
    "    return tuple(_out)\n"
    "def _sig(_o):\n"
    "    _vals = []\n"
    "    for _a in _SIGATTRS:\n"
    "        try:\n"
    "            _vals.append(str(getattr(_o, _a)))\n"
    "        except Exception:\n"
    "            _vals.append(None)\n"
    "    return (str(_o.GetType().Name), _scope_repr(_o), tuple(_vals))\n"
)


def dedupe_cfg(candidates_expr: str) -> str:
    """Statements (inside the create try) that swap a just-made _obj for an identical existing one."""
    return (
        "_dup = None\n"
        "if _REUSE:\n"
        "    _mine = _sig(_obj)\n"
        "    for _c in " + candidates_expr + ":\n"
        "        try:\n"
        "            if int(_c.ObjectId) == int(_obj.ObjectId):\n"
        "                continue\n"
        "            if _sig(_c) == _mine:\n"
        "                _dup = _c\n"
        "                break\n"
        "        except Exception:\n"
        "            pass\n"
        "if _dup is not None:\n"
        "    _obj.Delete()\n"
        "    _obj = _dup\n"
        "    _fresh = False\n"
    )


def dedupe_head(reuse: bool, sig_attrs) -> str:
    return (
        _DEDUP_DEFS
        + "_SIGATTRS = %r\n" % (tuple(sig_attrs),)
        + "_REUSE = %s\n" % ("True" if reuse else "False")
    )


def result_script(
    idx: int,
    adder: str,
    label: str,
    config: str = "",
    ns_name: str = "",
    extra_info: str = "",
    scope_attr: str = "Location",
    pre: str = "",
    reuse_existing: bool = True,
    sig_attrs=(),
    values: bool = True,
) -> str:
    """Add a result to the solution. Values are read back only when the solution is Done.

    Before the analysis is solved Mechanical reports 0 for everything, which used to be returned
    as if it were a result; now the object is created (it will evaluate after the solve) and the
    values are null with a note saying so. With reuse_existing an identical existing result
    (same type, scope and settings) is returned instead of creating a duplicate
    (already_exists: true, created: false).
    """
    if not isinstance(reuse_existing, bool):
        raise ValueError("reuse_existing must be true or false")
    head = (
        "import json\n"
        + SCOPE_TYPE_DEFS
        + pick_analysis(idx)
        + dedupe_head(reuse_existing, sig_attrs)
    )
    if ns_name:
        head += "_NS_NAME = " + lit(ns_name) + "\n" + NS_LOOKUP + _NS_NONEMPTY
    cfg = ""
    if ns_name:
        cfg += (
            "_stype = _scope_type(_ns)\n"
            "_obj.%s = _ns\n"
            "if _obj.%s is None:\n"
            "    raise Exception(u'Mechanical did not accept the selection as the scope of this result (the selection "
            "holds ' + str(_stype) + u' entities)')\n" % (scope_attr, scope_attr)
        )
    else:
        cfg += "_stype = None\n"
    cfg += config
    cfg += "_sol = _analysis.Solution\n" + dedupe_cfg("_sol.Children")
    cfg += "_supp = None\ntry:\n    _supp = bool(_obj.Suppressed)\nexcept Exception:\n    pass\n"
    cfg += "_status = str(_sol.Status)\n_solved = _status == 'Done'\n"
    if values:
        cfg += (
            "_vals = {'max': None, 'min': None}\n"
            "if _solved:\n"
            "    _sol.EvaluateAllResults()\n"
            "    _vals = {'max': str(_obj.Maximum), 'min': str(_obj.Minimum)}\n"
        )
    else:
        cfg += "if _solved:\n    _sol.EvaluateAllResults()\n"
    info = (
        "dict({'ok': True, 'created': _fresh, 'already_exists': not _fresh, 'type': "
        + lit(label)
        + ", 'name': str(_obj.Name), "
        "'solved': _solved, 'solution_status': _status, "
        + ("'max': _vals['max'], 'min': _vals['min'], " if values else "")
        + "'suppressed': _supp, 'object_state': str(_obj.ObjectState), 'scope_type': _stype, "
        + "'note': (None if _solved else "
        + lit(_NOT_SOLVED)
        + ")}"
        + (", **" + extra_info if extra_info else "")
        + ")"
    )
    body = create_block("_analysis.Solution." + adder + "()", cfg, info)
    if pre:
        # pre sets _pre_err (text) when the request cannot be met; nothing is created then
        body = (
            "_pre_err = None\n" + pre + "if _pre_err is not None:\n"
            "    print(json.dumps({'ok': False, 'error': _pre_err, 'created': False}))\n"
            "else:\n" + indent(body)
        )
    if ns_name:
        return (
            head + "if _ns is None:\n"
            "    print(json.dumps({'ok': False, 'error': _ns_err, 'created': False}))\n"
            "else:\n" + indent(body)
        )
    return head + body


_AXES = {"X": "XAxis", "Y": "YAxis", "Z": "ZAxis"}
_PRINCIPAL = {
    "max": ("AddMaximumPrincipalStress", "Maximum Principal Stress"),
    "mid": ("AddMiddlePrincipalStress", "Middle Principal Stress"),
    "min": ("AddMinimumPrincipalStress", "Minimum Principal Stress"),
}
_ENUMS = "Ansys.Mechanical.DataModel.Enums."


def equivalent_stress_script(
    idx: int, named_selection: str = "", reuse_existing: bool = True
) -> str:
    check_text("named_selection", named_selection)
    return result_script(
        idx,
        "AddEquivalentStress",
        "Equivalent (von Mises) Stress",
        ns_name=named_selection.strip() and named_selection,
        reuse_existing=reuse_existing,
    )


def directional_deformation_script(
    idx: int, axis: str, named_selection: str = "", reuse_existing: bool = True
) -> str:
    check_text("named_selection", named_selection)
    key = axis.strip().upper() if isinstance(axis, str) else ""
    if key not in _AXES:
        raise ValueError("axis must be X, Y or Z")
    cfg = "_obj.NormalOrientation = " + _ENUMS + "NormalOrientationType." + _AXES[key] + "\n"
    return result_script(
        idx,
        "AddDirectionalDeformation",
        "Directional Deformation " + key,
        cfg,
        ns_name=named_selection.strip() and named_selection,
        extra_info="{'axis': " + lit(key) + "}",
        reuse_existing=reuse_existing,
        sig_attrs=("NormalOrientation",),
    )


def principal_stress_script(idx: int, which: str, reuse_existing: bool = True) -> str:
    key = which.strip().lower() if isinstance(which, str) else ""
    if key not in _PRINCIPAL:
        raise ValueError("which must be max, mid or min")
    adder, label = _PRINCIPAL[key]
    return result_script(
        idx, adder, label, extra_info="{'which': " + lit(key) + "}", reuse_existing=reuse_existing
    )


def stress_tool_script(idx: int, reuse_existing: bool = True) -> str:
    """Stress Tool (its results are its own children; no Maximum/Minimum is read here)."""
    return result_script(
        idx, "AddStressTool", "Stress Tool", reuse_existing=reuse_existing, values=False
    )


def total_deformation_script(idx: int, mode: int, reuse_existing: bool = True) -> str:
    mode = integer("mode", mode, 0, 10_000)
    pre = ""
    cfg = ""
    if mode > 0:
        pre = (
            "if str(_analysis.AnalysisType) != 'Modal':\n"
            "    _pre_err = u'mode > 0 needs a Modal analysis; this one is ' + str(_analysis.AnalysisType)\n"
            "else:\n"
            "    try:\n"
            "        _nmodes = int(_analysis.AnalysisSettings.MaximumModesToFind)\n"
            "    except Exception:\n"
            "        _nmodes = None\n"
            "    if _nmodes is not None and %d > _nmodes:\n"
            "        _pre_err = u'mode %d exceeds the ' + str(_nmodes) + u' modes this analysis extracts'\n"
            % (mode, mode)
        )
        cfg = "_obj.Mode = %d\n" % mode
    return result_script(
        idx,
        "AddTotalDeformation",
        "Total Deformation",
        cfg,
        extra_info="{'mode': %d}" % mode,
        pre=pre,
        reuse_existing=reuse_existing,
        sig_attrs=("Mode",),
    )


def all_modes_script(idx: int, num_modes: int, reuse_existing: bool = True) -> str:
    """Total Deformation for modes 1..num_modes of a solved Modal analysis (all or nothing).
    Modes that already have a Total Deformation are kept, not duplicated (reuse_existing)."""
    n = integer("num_modes", num_modes, 1, 1000)
    if not isinstance(reuse_existing, bool):
        raise ValueError("reuse_existing must be true or false")
    return (
        "import json\n"
        + pick_analysis(idx)
        + "_N = %d\n" % n
        + "_REUSE = "
        + ("True" if reuse_existing else "False")
        + "\n"
        "_sol = _analysis.Solution\n"
        "_status = str(_sol.Status)\n"
        "_made = []\n"
        "_have = {}\n"
        "try:\n"
        "    if str(_analysis.AnalysisType) != 'Modal':\n"
        "        raise Exception('analysis ' + str(_IDX) + ' (' + str(_analysis.Name) + ') is a ' + "
        "str(_analysis.AnalysisType) + ' analysis, not a Modal analysis')\n"
        "    if _status != 'Done':\n"
        "        raise Exception('the modal analysis is not solved yet (status ' + _status + '); solve it first so the "
        "number of modes is known - nothing was created')\n"
        "    try:\n"
        "        _max = int(_analysis.AnalysisSettings.MaximumModesToFind)\n"
        "    except Exception:\n"
        "        _max = None\n"
        "    if _max is not None and _N > _max:\n"
        "        raise Exception('num_modes %d exceeds the %d modes this analysis extracts' % (_N, _max))\n"
        "    if _REUSE:\n"
        "        for _r in _sol.Children:\n"
        "            if 'TotalDeformation' in str(_r.GetType().Name):\n"
        "                try:\n"
        "                    _have[int(_r.Mode)] = _r\n"
        "                except Exception:\n"
        "                    pass\n"
        "    for _i in range(1, _N + 1):\n"
        "        if _i in _have:\n"
        "            continue\n"
        "        _td = _sol.AddTotalDeformation()\n"
        "        _made.append(_td)\n"
        "        _td.Mode = _i\n"
        "    _sol.EvaluateAllResults()\n"
        "    _freqs = []\n"
        "    for _i in range(1, _N + 1):\n"
        "        _td = _have.get(_i)\n"
        "        if _td is None:\n"
        "            _td = [_m for _m in _made if int(_m.Mode) == _i][0]\n"
        "        try:\n"
        "            _freqs.append(str(_td.ReportedFrequency))\n"
        "        except Exception:\n"
        "            _freqs.append(None)\n"
        "    _info = {'ok': True, 'created': len(_made) > 0, 'modes_added': len(_made), "
        "'modes_already_present': sorted(_have.keys()), 'frequencies': _freqs}\n"
        "except Exception as _e:\n"
        "    _left = 0\n"
        "    for _td in _made:\n"
        "        try:\n"
        "            _td.Delete()\n"
        "        except Exception:\n"
        "            _left += 1\n"
        "    _info = {'ok': False, 'error': str(_e), 'created': False}\n"
        "    if _made:\n"
        "        _info['rolled_back'] = _left == 0\n"
        "print(json.dumps(_info))\n"
    )


def modal_frequencies_script(idx: int, add_results_if_missing: bool) -> str:
    return (
        "import json\n"
        + pick_analysis(idx)
        + "_ADD = %s\n" % ("True" if add_results_if_missing else "False")
        + "_sol = _analysis.Solution\n"
        "_status = str(_sol.Status)\n"
        "_made = []\n"
        "def _collect():\n"
        "    _out = []\n"
        "    for _r in _sol.Children:\n"
        "        if 'TotalDeformation' not in str(_r.GetType().Name):\n"
        "            continue\n"
        "        try:\n"
        "            _q = _r.ReportedFrequency\n"
        "            try:\n"
        "                _hz = float(_q.ConvertUnit('Hz').Value)\n"
        "            except Exception:\n"
        "                _hz = float(str(_q).split()[0])\n"
        "            _m = int(_r.Mode)\n"
        "        except Exception:\n"
        "            continue\n"
        "        if _hz == _hz and abs(_hz) != float('inf'):\n"
        "            _out.append({'mode': _m, 'frequency_hz': _hz})\n"
        "    return _out\n"
        "try:\n"
        "    if str(_analysis.AnalysisType) != 'Modal':\n"
        "        raise Exception('analysis ' + str(_IDX) + ' (' + str(_analysis.Name) + ') is a ' + "
        "str(_analysis.AnalysisType) + ' analysis, not a Modal analysis')\n"
        "    if _status != 'Done':\n"
        "        raise Exception('the modal analysis is not solved yet (status ' + _status + '); solve it first')\n"
        "    try:\n"
        "        _n = int(_analysis.AnalysisSettings.MaximumModesToFind)\n"
        "    except Exception:\n"
        "        _n = 6\n"
        "    _freqs = _collect()\n"
        "    _present = set([_f['mode'] for _f in _freqs])\n"
        "    _missing = [_i for _i in range(1, _n + 1) if _i not in _present]\n"
        "    if _missing and _ADD:\n"
        "        for _i in _missing:\n"
        "            _td = _sol.AddTotalDeformation()\n"
        "            _made.append(_td)\n"
        "            _td.Mode = _i\n"
        "        _sol.EvaluateAllResults()\n"
        "        _freqs = _collect()\n"
        "        _present = set([_f['mode'] for _f in _freqs])\n"
        "        _missing = [_i for _i in range(1, _n + 1) if _i not in _present]\n"
        "    if not _freqs:\n"
        "        raise Exception('no modal frequencies could be read: the analysis has no Total Deformation "
        "results' + ('' if _ADD else ' (add_results_if_missing is False)'))\n"
        "    _freqs.sort(key=lambda x: x['mode'])\n"
        "    _info = {'ok': True, 'frequencies': _freqs, 'results_created': len(_made) > 0, 'unit': 'Hz', "
        "'modes_extracted': _n, 'partial': len(_missing) > 0, 'missing_modes': _missing}\n"
        "    if _missing:\n"
        "        _info['warning'] = u'only ' + str(len(_freqs)) + u' of ' + str(_n) + u' modes were read; "
        "pass add_results_if_missing=true to create the others'\n"
        "except Exception as _e:\n"
        "    _left = 0\n"
        "    for _td in _made:\n"
        "        try:\n"
        "            _td.Delete()\n"
        "        except Exception:\n"
        "            _left += 1\n"
        "    _info = {'ok': False, 'error': str(_e), 'solution_status': _status}\n"
        "    if _made:\n"
        "        _info['rolled_back'] = _left == 0\n"
        "print(json.dumps(_info))\n"
    )


def reaction_force_script(idx: int, named_selection: str, reuse_existing: bool = True) -> str:
    if not named_selection.strip():
        raise ValueError("named_selection is required")
    if not isinstance(reuse_existing, bool):
        raise ValueError("reuse_existing must be true or false")
    cfg = (
        "_stype = _scope_type(_ns)\n"
        "_obj.LocationMethod = " + _ENUMS + "LocationDefinitionMethod.GeometrySelection\n"
        "_obj.GeometryLocation = _ns\n"
        "if _obj.GeometryLocation is None:\n"
        "    raise Exception(u'Mechanical did not accept the selection as the scope of the probe (it holds ' + "
        "str(_stype) + u' entities)')\n"
        "try:\n"
        "    _scope = len(_obj.GeometryLocation.Ids)\n"
        "except Exception:\n"
        "    _scope = None\n"
        "if _scope is not None and _scope == 0:\n"
        "    raise Exception('the probe has an empty scope')\n"
        "_sol = _analysis.Solution\n" + dedupe_cfg("_sol.Children") + "_status = str(_sol.Status)\n"
        "_solved = _status == 'Done'\n"
        "_vals = {'x': None, 'y': None, 'z': None, 'total': None}\n"
        "if _solved:\n"
        "    _sol.EvaluateAllResults()\n"
        "    _vals = {'x': str(_obj.XAxis), 'y': str(_obj.YAxis), 'z': str(_obj.ZAxis), 'total': str(_obj.Total)}\n"
    )
    info = (
        "{'ok': True, 'created': _fresh, 'already_exists': not _fresh, 'type': 'Force Reaction', "
        "'name': str(_obj.Name), 'solved': _solved, "
        "'solution_status': _status, 'x': _vals['x'], 'y': _vals['y'], 'z': _vals['z'], "
        "'total': _vals['total'], 'scope_entities': _scope, 'scope_type': _stype, "
        "'object_state': str(_obj.ObjectState), "
        "'note': (None if _solved else " + lit(_NOT_SOLVED) + ")}"
    )
    body = create_block("_analysis.Solution.AddForceReaction()", cfg, info)
    return (
        "import json\n"
        + SCOPE_TYPE_DEFS
        + pick_analysis(idx)
        + dedupe_head(reuse_existing, ())
        + "_NS_NAME = "
        + lit(named_selection)
        + "\n"
        + NS_LOOKUP
        + _NS_NONEMPTY
        + "if _ns is None:\n"
        "    print(json.dumps({'ok': False, 'error': _ns_err, 'created': False}))\n"
        "else:\n" + indent(body)
    )


def stress_result_script(
    kind: str, axis: str = "", analysis_index: int = 0, reuse_existing: bool = True
) -> str:
    """Add a normal-stress (axis x/y/z) or stress-intensity result."""
    idx = index(analysis_index)
    if kind == "stress_intensity":
        return result_script(
            idx, "AddStressIntensity", "Stress Intensity", reuse_existing=reuse_existing
        )
    if kind == "normal_stress":
        a = axis.strip().lower() if isinstance(axis, str) else ""
        if a not in ("x", "y", "z"):
            raise ValueError("axis must be x, y or z")
        cfg = (
            "_obj.NormalOrientation = "
            + _ENUMS
            + "NormalOrientationType."
            + _AXES[a.upper()]
            + "\n"
        )
        return result_script(
            idx,
            "AddNormalStress",
            "Normal Stress " + a.upper(),
            cfg,
            extra_info="{'axis': " + lit(a.upper()) + "}",
            reuse_existing=reuse_existing,
            sig_attrs=("NormalOrientation",),
        )
    raise ValueError("kind must be normal_stress or stress_intensity")


# ---------------------------------------------------------------------------
# Model setup builders
# ---------------------------------------------------------------------------

_BEHAVIOR_2D = {
    "axisymmetric": "AxiSymmetric",
    "plane_strain": "PlaneStrain",
    "plane_stress": "PlaneStress",
}
_CONTACT_TYPES = {
    "bonded": "Bonded",
    "no_separation": "NoSeparation",
    "frictionless": "Frictionless",
    "rough": "Rough",
    "frictional": "Frictional",
}

#: Element size limits in mm (a size outside this is almost certainly a unit mistake).
SIZE_MIN_MM = 0.001
SIZE_MAX_MM = 1e5


def element_size_script(element_size_mm: float) -> str:
    """Set the global element size, read it back and fail (restoring the old size) if it differs.

    Mechanical ignores a negative size silently, so the old script reported the value it was
    given while the model kept the previous one.
    """
    size_mm = num("element_size_mm", element_size_mm, lo=SIZE_MIN_MM, hi=SIZE_MAX_MM)
    return (
        "import json\n"
        "_mesh = ExtAPI.DataModel.Project.Model.Mesh\n"
        "_want = %s\n" % fnum(size_mm / 1000.0) + "_old = _mesh.ElementSize\n"
        "_mesh.ElementSize = Quantity(_want, 'm')\n"
        "_got = float(_mesh.ElementSize.ConvertUnit('m').Value)\n"
        "if abs(_got - _want) > 1e-9 + 1e-6 * abs(_want):\n"
        "    _mesh.ElementSize = _old\n"
        "    print(json.dumps({'ok': False, 'error': 'Mechanical did not accept the element size (it reads ' + str(_got * 1000.0) "
        "+ ' mm instead of %s mm); the previous size was restored' }))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'element_size_mm': _got * 1000.0}))\n" % fnum(size_mm)
    )


def set_2d_behavior_script(behavior: str) -> str:
    key = behavior.strip().lower() if isinstance(behavior, str) else ""
    if key not in _BEHAVIOR_2D:
        raise ValueError("behavior must be one of: " + ", ".join(sorted(_BEHAVIOR_2D)))
    return (
        "import json\n"
        "_geo = ExtAPI.DataModel.Project.Model.Geometry\n"
        "try:\n"
        "    _vis = bool(_geo.IsPropertyVisible('Model2DBehavior'))\n"
        "except Exception:\n"
        "    _vis = True\n"
        "if not _vis:\n"
        "    print(json.dumps({'ok': False, 'error': 'this model has no 2D geometry, so there is no 2D behavior to set'}))\n"
        "else:\n"
        "    _old = _geo.Model2DBehavior\n"
        "    _geo.Model2DBehavior = " + _ENUMS + "Model2DBehavior." + _BEHAVIOR_2D[key] + "\n"
        "    _got = str(_geo.Model2DBehavior)\n"
        "    if _got != " + repr(_BEHAVIOR_2D[key]) + ":\n"
        "        _geo.Model2DBehavior = _old\n"
        "        print(json.dumps({'ok': False, 'error': '2D behavior reads ' + _got + ' after setting it; restored'}))\n"
        "    else:\n"
        "        print(json.dumps({'ok': True, 'behavior': _got}))\n"
    )


_BODY_WALK = (
    "_bodies = [_b for _b in ExtAPI.DataModel.Project.Model.GetChildren("
    "DataModelObjectCategory.Body, True) if str(_b.Name) == _BODY_NAME]\n"
)


def body_sizing_script(body_name: str, element_size_mm: float) -> str:
    """One sizing control for every body of that name; an existing one is updated, not duplicated."""
    check_text("body_name", body_name)
    if not body_name.strip():
        raise ValueError("body_name is required")
    size_mm = num("element_size_mm", element_size_mm, lo=SIZE_MIN_MM, hi=SIZE_MAX_MM)
    want = fnum(size_mm / 1000.0)
    check = (
        "_got = float(%s.ElementSize.ConvertUnit('m').Value)\n"
        "if abs(_got - " + want + ") > 1e-9 + 1e-6 * " + want + ":\n"
        "    raise Exception('Mechanical did not accept the sizing (it reads ' + str(_got * 1000.0) + ' mm)')\n"
    )
    cfg = (
        "_sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "_sel.Ids = [int(_b.GetGeoBody().Id) for _b in _bodies]\n"
        "_obj.Location = _sel\n"
        "_obj.Name = _TAG\n"
        "_obj.ElementSize = Quantity(" + want + ", 'm')\n" + check % "_obj"
    )
    info = (
        "{'ok': True, 'created': True, 'reused': False, 'name': str(_obj.Name), 'bodies': len(_bodies), "
        "'element_size_mm': _got * 1000.0}"
    )
    create = create_block("_model.Mesh.AddSizing()", cfg, info)
    update = (
        "_prev = _existing.ElementSize\n"
        "_prev_ids = None\n"
        "try:\n"
        "    _prev_ids = list(_existing.Location.Ids)\n"
        "    _sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "    _sel.Ids = [int(_b.GetGeoBody().Id) for _b in _bodies]\n"
        "    _existing.Location = _sel\n"
        "    _existing.ElementSize = Quantity("
        + want
        + ", 'm')\n"
        + indent(check % "_existing")
        + "    print(json.dumps({'ok': True, 'created': False, 'reused': True, 'name': str(_existing.Name), "
        "'bodies': len(_bodies), 'element_size_mm': _got * 1000.0}))\n"
        "except Exception as _e:\n"
        "    try:\n"
        "        _existing.ElementSize = _prev\n"
        "        if _prev_ids is not None:\n"
        "            _sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "            _sel.Ids = _prev_ids\n"
        "            _existing.Location = _sel\n"
        "    except Exception:\n"
        "        pass\n"
        "    print(json.dumps({'ok': False, 'error': str(_e), 'created': False, 'restored': True}))\n"
    )
    return (
        "import json\n"
        "_model = ExtAPI.DataModel.Project.Model\n"
        "_BODY_NAME = " + lit(body_name) + "\n"
        "_TAG = u'MCP body size: ' + _BODY_NAME\n" + _BODY_WALK + "_existing = None\n"
        "for _s in _model.Mesh.GetChildren(DataModelObjectCategory.Sizing, True):\n"
        "    if str(_s.Name) == _TAG:\n"
        "        _existing = _s\n"
        "        break\n"
        "if not _bodies:\n"
        "    print(json.dumps({'ok': False, 'error': u'Body not found: ' + _BODY_NAME, 'created': False}))\n"
        "elif _existing is not None:\n" + indent(update) + "else:\n" + indent(create)
    )


def nonlinear_settings_script(
    analysis_index: int = 0,
    large_deflection: Optional[bool] = None,
    auto_time_stepping: Optional[bool] = None,
    initial_substeps: Optional[int] = None,
    min_substeps: Optional[int] = None,
    max_substeps: Optional[int] = None,
    number_of_steps: int = 0,
) -> str:
    """Load-step control for a nonlinear static solve. Only the arguments given are changed."""
    idx = index(analysis_index)
    subs = [initial_substeps, min_substeps, max_substeps]
    for nm, v in zip(("initial_substeps", "min_substeps", "max_substeps"), subs):
        if v is not None:
            integer(nm, v, 1, 100_000)
    if all(v is not None for v in subs) and not (min_substeps <= initial_substeps <= max_substeps):
        raise ValueError("need min_substeps <= initial_substeps <= max_substeps")
    if min_substeps is not None and max_substeps is not None and min_substeps > max_substeps:
        raise ValueError("need min_substeps <= max_substeps")
    if (
        initial_substeps is not None
        and max_substeps is not None
        and initial_substeps > max_substeps
    ):
        raise ValueError("need initial_substeps <= max_substeps")
    if (
        initial_substeps is not None
        and min_substeps is not None
        and initial_substeps < min_substeps
    ):
        raise ValueError("need min_substeps <= initial_substeps")
    integer("number_of_steps", number_of_steps, 0, 10_000)
    for nm, v in (
        ("large_deflection", large_deflection),
        ("auto_time_stepping", auto_time_stepping),
    ):
        if v is not None and not isinstance(v, bool):
            raise ValueError("%s must be true or false" % nm)

    sets: List[str] = []  # (attribute, python value source)
    if number_of_steps > 0:
        sets.append(("NumberOfSteps", str(number_of_steps)))
    if large_deflection is not None:
        sets.append(("LargeDeflection", "True" if large_deflection else "False"))
    if auto_time_stepping is not None:
        sets.append(
            (
                "AutomaticTimeStepping",
                _ENUMS + "AutomaticTimeStepping." + ("On" if auto_time_stepping else "Off"),
            )
        )
    if any(v is not None for v in subs):
        sets.append(("DefineBy", _ENUMS + "TimeStepDefineByType.Substeps"))
    if initial_substeps is not None:
        sets.append(("InitialSubsteps", str(initial_substeps)))
    if min_substeps is not None:
        sets.append(("MinimumSubsteps", str(min_substeps)))
    if max_substeps is not None:
        sets.append(("MaximumSubsteps", str(max_substeps)))
    if not sets:
        raise ValueError("nothing to change: pass at least one setting")

    names = [a for a, _ in sets]
    given_subs = {
        k: v
        for k, v in (
            ("InitialSubsteps", initial_substeps),
            ("MinimumSubsteps", min_substeps),
            ("MaximumSubsteps", max_substeps),
        )
        if v is not None
    }
    out = (
        "import json\n" + pick_analysis(idx) + "_s = _analysis.AnalysisSettings\n"
        "_names = " + repr(names) + "\n"
        "_old = {}\n"
        "_bad = None\n"
        "if _s is None:\n"
        "    _bad = u'analysis ' + str(_IDX) + u' (' + str(_analysis.Name) + u', type ' + str(_analysis.AnalysisType) "
        "+ u') has no nonlinear AnalysisSettings to change'\n"
        "else:\n"
        "    for _n in _names:\n"
        "        try:\n"
        "            _old[_n] = getattr(_s, _n)\n"
        "        except Exception:\n"
        "            _bad = u'setting ' + _n + u' is not available for ' + str(_analysis.AnalysisType) + u' analyses'\n"
        "            break\n"
        "    _SUBS = " + repr(given_subs) + "\n"
        "    if _bad is None and _SUBS:\n"
        "        _mg = {}\n"
        "        for _k in ('InitialSubsteps', 'MinimumSubsteps', 'MaximumSubsteps'):\n"
        "            try:\n"
        "                _mg[_k] = int(_SUBS[_k]) if _k in _SUBS else int(getattr(_s, _k))\n"
        "            except Exception:\n"
        "                _mg[_k] = None\n"
        "        if None not in _mg.values() and not (_mg['MinimumSubsteps'] <= _mg['InitialSubsteps'] <= _mg['MaximumSubsteps']):\n"
        "            _bad = (u'the substeps would be initial ' + str(_mg['InitialSubsteps']) + u', minimum ' + "
        "str(_mg['MinimumSubsteps']) + u', maximum ' + str(_mg['MaximumSubsteps']) + u' (including the values "
        "already set): need minimum <= initial <= maximum')\n"
        "if _bad is not None:\n"
        "    print(json.dumps({'ok': False, 'error': _bad}))\n"
        "else:\n"
        "  try:\n"
    )
    for attr, src in sets:
        out += "      _s.%s = %s\n" % (attr, src)
    out += (
        "      _now = {}\n"
        "      for _n in _names:\n"
        "          _now[_n] = str(getattr(_s, _n))\n"
        "      print(json.dumps({'ok': True, 'applied': _names, 'values': _now}))\n"
        "  except Exception as _e:\n"
        "      for _n in _names:\n"
        "          try:\n"
        "              setattr(_s, _n, _old[_n])\n"
        "          except Exception:\n"
        "              pass\n"
        "      print(json.dumps({'ok': False, 'error': str(_e), 'restored': True}))\n"
    )
    return out


def _check_contact(contact_type: str, friction_coefficient: float):
    key = contact_type.strip().lower() if isinstance(contact_type, str) else ""
    if key not in _CONTACT_TYPES:
        raise ValueError("contact_type must be one of: " + ", ".join(sorted(_CONTACT_TYPES)))
    mu = num("friction_coefficient", friction_coefficient, lo=0.0, hi=10.0)
    if key == "frictional" and mu <= 0:
        raise ValueError("frictional contact needs friction_coefficient > 0")
    return key, mu


def parse_names(text: str, what: str) -> List[str]:
    """Comma-separated exact names -> list (blank entries dropped)."""
    check_text(what, text)
    return [p.strip() for p in text.split(",") if p.strip()]


def set_contact_type_script(
    contact_type: str,
    friction_coefficient: float = 0.0,
    region_names: str = "",
    only_if_currently: str = "",
    include_suppressed: bool = False,
) -> str:
    """Set the type (and, for frictional, the friction coefficient) on contact regions already in
    the model (e.g. the auto-detected ones).

    With no filter this overwrites EVERY region, including ones configured by hand; region_names
    (exact names) and only_if_currently (a contact type) limit it. Region names are not unique in
    Mechanical: a name shared by several regions matches all of them (a warning says so). Both
    properties the call can change are snapshotted before and restored if anything fails; the
    result lists only regions that really changed, with the type and friction before and after.
    """
    key, mu = _check_contact(contact_type, friction_coefficient)
    if not isinstance(include_suppressed, bool):
        raise ValueError("include_suppressed must be true or false")
    names = parse_names(region_names, "region_names")
    cur = ""
    if only_if_currently.strip():
        cur = only_if_currently.strip().lower()
        if cur not in _CONTACT_TYPES:
            raise ValueError(
                "only_if_currently must be one of: " + ", ".join(sorted(_CONTACT_TYPES))
            )
    want = _CONTACT_TYPES[key]
    return (
        "import json\n"
        "_INC = " + ("True" if include_suppressed else "False") + "\n"
        "_sup_skipped = []\n"
        "_sup_changed = []\n"
        "_NAMES = [" + ", ".join(lit(n) for n in names) + "]\n"
        "_CUR = " + repr(_CONTACT_TYPES[cur] if cur else "") + "\n"
        "_WANT = " + repr(want) + "\n"
        "_MU = " + (fnum(mu) if key == "frictional" else "None") + "\n"
        "_changed = []\n"
        "_unchanged = []\n"
        "_skipped = 0\n"
        "_done = []\n"
        "def _mu_of(_c):\n"
        "    try:\n"
        "        return float(_c.FrictionCoefficient)\n"
        "    except Exception:\n"
        "        return None\n"
        "_all = list(ExtAPI.DataModel.Project.Model.GetChildren("
        "DataModelObjectCategory.ContactRegion, True))\n"
        "_have = [str(_c.Name) for _c in _all]\n"
        "_missing = [_n for _n in _NAMES if _n not in _have]\n"
        "_warn = []\n"
        "for _n in _NAMES:\n"
        "    if _have.count(_n) > 1:\n"
        "        _warn.append(str(_have.count(_n)) + u' contact regions are named ' + _n + u'; all of them were matched')\n"
        "if _missing:\n"
        "    print(json.dumps({'ok': False, 'error': u'No contact region named: ' + u', '.join(_missing)}))\n"
        "elif not _all:\n"
        "    print(json.dumps({'ok': False, 'error': 'no contact regions in the model'}))\n"
        "else:\n"
        "    try:\n"
        "        for _c in _all:\n"
        "            if _NAMES and str(_c.Name) not in _NAMES:\n"
        "                _skipped += 1\n"
        "                continue\n"
        "            if _CUR and str(_c.ContactType) != _CUR:\n"
        "                _skipped += 1\n"
        "                continue\n"
        "            try:\n"
        "                _sup = bool(_c.Suppressed)\n"
        "            except Exception:\n"
        "                _sup = False\n"
        "            if _sup and not _INC:\n"
        "                _sup_skipped.append(str(_c.Name))\n"
        "                continue\n"
        "            _mu0 = _mu_of(_c)\n"
        "            _same_type = str(_c.ContactType) == _WANT\n"
        "            _same_mu = _MU is None or (_mu0 is not None and abs(_mu0 - _MU) < 1e-12)\n"
        "            if _same_type and _same_mu:\n"
        "                _unchanged.append(str(_c.Name))\n"
        "                continue\n"
        "            _t0 = _c.ContactType\n"
        "            _done.append((_c, _t0, _mu0))\n"
        "            _c.ContactType = ContactType."
        + want
        + "\n"
        + ("            _c.FrictionCoefficient = " + fnum(mu) + "\n" if key == "frictional" else "")
        + "            if str(_c.ContactType) != _WANT:\n"
        "                raise Exception('region ' + str(_c.Name) + ' reads ' + str(_c.ContactType) + ' after setting it')\n"
        "            _changed.append({'name': str(_c.Name), 'was': str(_t0), 'now': _WANT, "
        "'friction_was': _mu0, 'friction_now': (_MU if _MU is not None else _mu0), 'suppressed': _sup})\n"
        "            if _sup:\n"
        "                _sup_changed.append(str(_c.Name))\n"
        "        if not _changed and not _unchanged and not _sup_skipped:\n"
        "            raise Exception('no contact region matched the filter')\n"
        "        print(json.dumps({'ok': True, 'regions_changed': len(_changed), 'regions_unchanged': len(_unchanged), "
        "'regions_skipped': _skipped, 'contact_type': _WANT, 'changed': _changed, 'unchanged': _unchanged, "
        "'warnings': _warn, 'suppressed_regions_changed': _sup_changed, "
        "'suppressed_regions_skipped': _sup_skipped}))\n"
        "    except Exception as _e:\n"
        "        for _c, _t, _m in _done:\n"
        "            try:\n"
        "                _c.ContactType = _t\n"
        "            except Exception:\n"
        "                pass\n"
        "            if _m is not None:\n"
        "                try:\n"
        "                    _c.FrictionCoefficient = _m\n"
        "                except Exception:\n"
        "                    pass\n"
        "        print(json.dumps({'ok': False, 'error': str(_e), 'restored': True}))\n"
    )


def add_contact_script(
    source_locator: str,
    target_locator: str,
    contact_type: str,
    friction_coefficient: float = 0.0,
    reuse_existing: bool = True,
    update_existing: bool = False,
) -> str:
    """source_locator / target_locator are location_snippet(...) outputs for the two sides.

    A region that already joins exactly these entities is found by its scope. If it also has the
    same type and (for frictional) the same friction coefficient it is returned as it is
    (already_exists: true). If it differs, the call is refused with the differences, unless
    update_existing=true, which changes the type/friction explicitly (and restores them on
    failure) and reports `updated`. Behaviour, formulation and the suppression state are not set
    by this call; they are reported (existing_properties, suppressed) so the caller can see them."""
    key, mu = _check_contact(contact_type, friction_coefficient)
    if not isinstance(reuse_existing, bool) or not isinstance(update_existing, bool):
        raise ValueError("reuse_existing and update_existing must be true or false")
    want = _CONTACT_TYPES[key]
    cfg = (
        "_obj.SourceLocation = _src\n"
        "_obj.TargetLocation = _tgt\n"
        "_obj.ContactType = ContactType."
        + want
        + "\n"
        + ("_obj.FrictionCoefficient = " + fnum(mu) + "\n" if key == "frictional" else "")
        + "_dup = None\n"
        "_updated = {}\n"
        "if _REUSE:\n"
        "    _mine = _scope_repr(_obj)\n"
        "    for _c in list(_model.GetChildren(DataModelObjectCategory.ContactRegion, True)):\n"
        "        try:\n"
        "            if int(_c.ObjectId) == int(_obj.ObjectId):\n"
        "                continue\n"
        "            if _scope_repr(_c) == _mine:\n"
        "                _dup = _c\n"
        "                break\n"
        "        except Exception:\n"
        "            pass\n"
        "if _dup is not None:\n"
        "    _obj.Delete()\n"
        "    _obj = _dup\n"
        "    _fresh = False\n"
        "    _diff = {}\n"
        "    if str(_dup.ContactType) != _WANT:\n"
        "        _diff['contact_type'] = [str(_dup.ContactType), _WANT]\n"
        "    if _MU is not None:\n"
        "        try:\n"
        "            _have = float(_dup.FrictionCoefficient)\n"
        "        except Exception:\n"
        "            _have = None\n"
        "        if _have is None or abs(_have - _MU) > 1e-12:\n"
        "            _diff['friction_coefficient'] = [_have, _MU]\n"
        "    if _diff and not _UPDATE:\n"
        "        raise Exception(u'a contact region (' + str(_dup.Name) + u') already joins these entities but differs: ' "
        "+ json.dumps(_diff) + u'. Pass update_existing=true to change it, or reuse_existing=false to add another')\n"
        "    if _diff:\n"
        "        _p_type = _dup.ContactType\n"
        "        try:\n"
        "            _p_mu = _dup.FrictionCoefficient\n"
        "        except Exception:\n"
        "            _p_mu = None\n"
        "        try:\n"
        "            _dup.ContactType = ContactType."
        + want
        + "\n"
        + (
            "            _dup.FrictionCoefficient = " + fnum(mu) + "\n"
            if key == "frictional"
            else ""
        )
        + "            if str(_dup.ContactType) != _WANT:\n"
        "                raise Exception('Mechanical holds ' + str(_dup.ContactType) + ' after the update')\n"
        "        except Exception:\n"
        "            _dup.ContactType = _p_type\n"
        "            if _p_mu is not None:\n"
        "                try:\n"
        "                    _dup.FrictionCoefficient = _p_mu\n"
        "                except Exception:\n"
        "                    pass\n"
        "            raise\n"
        "        _updated = _diff\n"
        "_ep = {}\n"
        "for _a in ('Behavior', 'ContactFormulation'):\n"
        "    try:\n"
        "        _ep[_a] = str(getattr(_obj, _a))\n"
        "    except Exception:\n"
        "        pass\n"
        "_supp = None\n"
        "try:\n"
        "    _supp = bool(_obj.Suppressed)\n"
        "except Exception:\n"
        "    pass\n"
    )
    info = (
        "{'ok': True, 'created': _fresh, 'already_exists': not _fresh, 'type': 'Contact Region', "
        "'name': str(_obj.Name), 'contact_type': str(_obj.ContactType), 'suppressed': _supp, "
        "'updated': _updated, 'existing_properties': _ep, 'warnings': _src_warn + _tgt_warn}"
    )
    create = create_block("_model.Connections.AddContactRegion()", cfg, info)
    return (
        "import json\n"
        "_model = ExtAPI.DataModel.Project.Model\n"
        + dedupe_head(reuse_existing, ())
        + "_WANT = "
        + repr(want)
        + "\n"
        "_MU = " + (fnum(mu) if key == "frictional" else "None") + "\n"
        "_UPDATE = "
        + ("True" if update_existing else "False")
        + "\n"
        + source_locator
        + "_src, _src_err, _src_warn = _ns, _ns_err, list(_ns_warn)\n"
        + target_locator
        + "_tgt, _tgt_err, _tgt_warn = _ns, _ns_err, list(_ns_warn)\n"
        "if _src is None:\n"
        "    print(json.dumps({'ok': False, 'error': u'source: ' + _src_err, 'created': False}))\n"
        "elif _tgt is None:\n"
        "    print(json.dumps({'ok': False, 'error': u'target: ' + _tgt_err, 'created': False}))\n"
        "elif _model.Connections is None:\n"
        "    print(json.dumps({'ok': False, 'error': 'model has no Connections object (needs two or more bodies)', "
        "'created': False}))\n"
        "else:\n" + indent(create)
    )


# ---------------------------------------------------------------------------
# Materials, suppression, deletion, named selections
# ---------------------------------------------------------------------------


def assign_material_script(body_name: str, material_name: str) -> str:
    check_text("body_name", body_name)
    check_text("material_name", material_name)
    if not body_name.strip() or not material_name.strip():
        raise ValueError("body_name and material_name are required")
    return (
        "import json\n"
        "_model = ExtAPI.DataModel.Project.Model\n"
        "_BODY_NAME = " + lit(body_name) + "\n"
        "_MAT = "
        + lit(material_name)
        + "\n"
        + _BODY_WALK
        + "_mats = [str(_m.Name) for _m in _model.Materials.Children]\n"
        "if not _bodies:\n"
        "    print(json.dumps({'ok': False, 'error': u'Body not found: ' + _BODY_NAME}))\n"
        "elif _MAT not in _mats:\n"
        "    print(json.dumps({'ok': False, 'error': u'Material not in Engineering Data: ' + _MAT, "
        "'available': _mats[:60]}))\n"
        "else:\n"
        "    _done = []\n"
        "    try:\n"
        "        for _b in _bodies:\n"
        "            _done.append((_b, _b.Material))\n"
        "            _b.Material = _MAT\n"
        "            if str(_b.Material) != _MAT:\n"
        "                raise Exception('body ' + str(_b.Name) + ' reads material ' + str(_b.Material) + ' after assignment')\n"
        "        print(json.dumps({'ok': True, 'body': _BODY_NAME, 'material': _MAT, 'bodies_changed': len(_bodies)}))\n"
        "    except Exception as _e:\n"
        "        for _b, _m in _done:\n"
        "            try:\n"
        "                _b.Material = _m\n"
        "            except Exception:\n"
        "                pass\n"
        "        print(json.dumps({'ok': False, 'error': str(_e), 'restored': True}))\n"
    )


def suppress_script(name_prefix: str, suppress: bool, all_bodies: bool) -> str:
    check_text("name_prefix", name_prefix)
    if all_bodies:
        if name_prefix:
            raise ValueError("pass either name_prefix or all_bodies=true, not both")
    elif not name_prefix.strip():
        raise ValueError(
            "name_prefix is required (an empty prefix would match every body); "
            "pass all_bodies=true to change every body on purpose"
        )
    want = "True" if suppress else "False"
    return (
        "import json\n"
        "_PREFIX = " + lit(name_prefix) + "\n"
        "_ALL = " + ("True" if all_bodies else "False") + "\n"
        "_hits = [_b for _b in ExtAPI.DataModel.Project.Model.GetChildren(DataModelObjectCategory.Body, True)\n"
        "         if _ALL or str(_b.Name).startswith(_PREFIX)]\n"
        "if not _hits:\n"
        "    print(json.dumps({'ok': False, 'error': u'No body name starts with: ' + _PREFIX, 'affected': 0}))\n"
        "else:\n"
        "    _done = []\n"
        "    try:\n"
        "        for _b in _hits:\n"
        "            _done.append((_b, _b.Suppressed))\n"
        "            _b.Suppressed = " + want + "\n"
        "            if bool(_b.Suppressed) != " + want + ":\n"
        "                raise Exception('body ' + str(_b.Name) + ' did not change state')\n"
        "        print(json.dumps({'ok': True, 'suppressed': " + want + ", 'affected': len(_hits), "
        "'bodies': [str(_b.Name) for _b in _hits[:200]]}))\n"
        "    except Exception as _e:\n"
        "        for _b, _s in _done:\n"
        "            try:\n"
        "                _b.Suppressed = _s\n"
        "            except Exception:\n"
        "                pass\n"
        "        print(json.dumps({'ok': False, 'error': str(_e), 'restored': True}))\n"
    )


# Names as plain ASCII-safe text even when the model holds something undecodable (a lone
# surrogate in a name makes Mechanical fail to encode it): such characters come back as \uXXXX.
ESC_NAME_DEFS = (
    "def _en(_x):\n"
    "    _t = str(_x)\n"
    "    _out = []\n"
    "    _i = 0\n"
    "    _n = len(_t)\n"
    "    while _i < _n:\n"
    "        _c = ord(_t[_i])\n"
    "        if 0xD800 <= _c <= 0xDBFF and _i + 1 < _n and 0xDC00 <= ord(_t[_i + 1]) <= 0xDFFF:\n"
    "            _out.append(_t[_i])\n"
    "            _out.append(_t[_i + 1])\n"
    "            _i += 2\n"
    "            continue\n"
    "        if 0xD800 <= _c <= 0xDFFF:\n"
    "            _out.append(u'\\\\u%04x' % _c)\n"
    "        else:\n"
    "            _out.append(_t[_i])\n"
    "        _i += 1\n"
    "    return u''.join(_out)\n"
)

# Objects that are scoped to a named selection (loads, results, probes, contacts, point masses,
# mesh controls). Deleting the selection leaves them with no scope, silently, so it is refused.
DEPS_DEFS = (
    "def _refs(_o, _idset):\n"
    "    for _a in ('Location', 'GeometryLocation', 'SourceLocation', 'TargetLocation'):\n"
    "        try:\n"
    "            _L = getattr(_o, _a)\n"
    "            if _L is not None and int(_L.ObjectId) in _idset:\n"
    "                return True\n"
    "        except Exception:\n"
    "            pass\n"
    "    return False\n"
    "def _dependents(_idset):\n"
    "    _m = ExtAPI.DataModel.Project.Model\n"
    "    _c = []\n"
    "    for _a in _m.Analyses:\n"
    "        for _o in list(_a.Children):\n"
    "            _c.append((str(_a.Name), _o))\n"
    "        try:\n"
    "            for _o in list(_a.Solution.Children):\n"
    "                _c.append((str(_a.Name), _o))\n"
    "        except Exception:\n"
    "            pass\n"
    "    for _o in _m.Geometry.GetChildren(DataModelObjectCategory.PointMass, True):\n"
    "        _c.append(('Geometry', _o))\n"
    "    for _o in _m.GetChildren(DataModelObjectCategory.ContactRegion, True):\n"
    "        _c.append(('Connections', _o))\n"
    "    try:\n"
    "        for _o in list(_m.Mesh.Children):\n"
    "            _c.append(('Mesh', _o))\n"
    "    except Exception:\n"
    "        pass\n"
    "    _out = []\n"
    "    for _w, _o in _c:\n"
    "        if _refs(_o, _idset):\n"
    "            _out.append({'name': str(_o.Name), 'type': str(_o.GetType().Name), 'in': _w})\n"
    "    return _out\n"
)


def delete_named_selection_script(name: str, force: bool = False, object_id: int = 0) -> str:
    """Delete every named selection with this name, or the single one with this ObjectId (the id
    that list_named_selections reports; it works for a name that cannot be typed)."""
    check_text("name", name)
    object_id = integer("object_id", object_id, 0, 2_000_000_000)
    if not name.strip() and not object_id:
        raise ValueError("give a name or an object_id")
    if not isinstance(force, bool):
        raise ValueError("force must be true or false")
    return (
        "import json\n" + DEPS_DEFS + ESC_NAME_DEFS + "_NS_NAME = " + lit(name) + "\n"
        "_OID = %d\n" % object_id + "_FORCE = " + ("True" if force else "False") + "\n"
        "_all = [_n for _n in ExtAPI.DataModel.Project.Model.GetChildren(DataModelObjectCategory.NamedSelection, True)"
        " if (int(_n.ObjectId) == _OID if _OID else str(_n.Name) == _NS_NAME)]\n"
        "if not _all:\n"
        "    print(json.dumps({'ok': False, 'error': u'Not found: ' + (str(_OID) if _OID else _NS_NAME)}))\n"
        "else:\n"
        "    _deps = _dependents(set([int(_n.ObjectId) for _n in _all]))\n"
        "    _label = _en(_all[0].Name)\n"
        "    if _deps and not _FORCE:\n"
        "        print(json.dumps({'ok': False, 'error': str(len(_deps)) + u' object(s) are scoped to this named selection "
        "and would be left without a scope; delete or re-scope them first, or pass force=true', "
        "'dependents': _deps[:50]}))\n"
        "    else:\n"
        "        _n_del = 0\n"
        "        for _n in _all:\n"
        "            _n.Delete()\n"
        "            _n_del += 1\n"
        "        print(json.dumps({'ok': True, 'deleted': _label, 'deleted_count': _n_del, "
        "'orphaned': _deps[:50]}))\n"
    )


def create_named_selection_script(
    name: str,
    body_names: str = "",
    body_prefix: str = "",
    entity_ids: str = "",
    box_mm: str = "",
    entity_type: str = "face",
    body_name: str = "",
    fully_inside: bool = False,
    replace: bool = False,
    allow_missing: bool = False,
) -> str:
    """Create a named selection from bodies (exact names or a prefix), geometry ids or a box.

    Nothing is created for an empty result; an existing name is refused unless replace=true
    (the old one is removed only after the new one is verified, and not at all if objects are
    scoped to it)."""
    check_text("name", name)
    check_text("body_names", body_names)
    check_text("body_prefix", body_prefix)
    if not name.strip():
        raise ValueError("name is required")
    if len(name) > 255:
        raise ValueError("name may not exceed 255 characters")
    if not isinstance(replace, bool) or not isinstance(allow_missing, bool):
        raise ValueError("replace and allow_missing must be true or false")
    given = [
        bool(body_names.strip()),
        bool(body_prefix.strip()),
        bool(entity_ids.strip()),
        bool(box_mm.strip()),
    ]
    if sum(given) != 1:
        raise ValueError("give exactly one of body_names, body_prefix, entity_ids, box_mm")
    head = "import json\n" + DEPS_DEFS + "_NAME = " + lit(name) + "\n"
    head += "_REPLACE = " + ("True" if replace else "False") + "\n"
    head += "_ALLOW_MISSING = " + ("True" if allow_missing else "False") + "\n"
    head += "_missing = []\n"
    if given[0] or given[1]:
        if given[0]:
            wanted = parse_names(body_names, "body_names")
            test = "str(_body.Name) in _WANT"
            head += "_WANT = [" + ", ".join(lit(n) for n in wanted) + "]\n"
        else:
            test = "str(_body.Name).startswith(_WANT)"
            head += "_WANT = " + lit(body_prefix) + "\n"
        head += (
            "_ns_warn = []\n"
            "_ids = []\n"
            "_seen = []\n"
            "_kind = 'body'\n"
            "for _asm in ExtAPI.DataModel.GeoData.Assemblies:\n"
            "    for _part in _asm.Parts:\n"
            "        for _body in _part.Bodies:\n"
            "            if " + test + ":\n"
            "                _ids.append(int(_body.Id))\n"
            "                _seen.append(str(_body.Name))\n"
            "_ns_err = u'No body matched'\n"
            + (
                "_missing = [_n for _n in _WANT if _n not in _seen]\n"
                "if _missing and not _ALLOW_MISSING:\n"
                "    _ids = []\n"
                "    _ns_err = u'Body name(s) not found: ' + u', '.join(_missing) + u' (nothing was created; "
                "allow_missing=true creates the selection from the others)'\n"
                if given[0]
                else ""
            )
        )
    else:
        head += (
            location_snippet("", entity_ids, box_mm, entity_type, body_name, fully_inside)
            + "_ids = []\n"
            "if _ns is not None:\n"
            "    _ids = [int(_i) for _i in _ns.Ids]\n"
            "_kind = " + repr("mixed" if given[2] else entity_type) + "\n"
        )
    return (
        head + "if not _ids:\n"
        "    print(json.dumps({'ok': False, 'error': _ns_err, 'created': False}))\n"
        "else:\n"
        "    _model = ExtAPI.DataModel.Project.Model\n"
        "    _old = [_n for _n in _model.GetChildren(DataModelObjectCategory.NamedSelection, True) if str(_n.Name) == _NAME]\n"
        "    _deps = _dependents(set([int(_n.ObjectId) for _n in _old])) if _old else []\n"
        "    if _old and not _REPLACE:\n"
        "        print(json.dumps({'ok': False, 'created': False, 'error': u'A named selection called ' + _NAME + "
        "u' already exists; pass replace=true to replace it'}))\n"
        "    elif _deps:\n"
        "        print(json.dumps({'ok': False, 'created': False, 'error': u'Cannot replace: ' + str(len(_deps)) + "
        "u' object(s) are scoped to the existing selection', 'dependents': _deps[:50]}))\n"
        "    else:\n"
        "        _new = None\n"
        "        try:\n"
        "            _new = _model.AddNamedSelection()\n"
        "            _sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "            _sel.Ids = _ids\n"
        "            _new.Location = _sel\n"
        "            _new.Name = _NAME\n"
        "            _cnt = len(_new.Location.Ids)\n"
        "            if _cnt != len(_ids) or str(_new.Name) != _NAME:\n"
        "                raise Exception('Mechanical holds ' + str(_cnt) + ' entities named ' + str(_new.Name) + ' instead of ' "
        "+ str(len(_ids)) + ' named ' + _NAME)\n"
        "            for _n in _old:\n"
        "                _n.Delete()\n"
        "            print(json.dumps({'ok': True, 'created': True, 'name': str(_new.Name), 'entities': _cnt, "
        "'entity_type': _kind, 'replaced': len(_old), 'warnings': _ns_warn, 'missing': _missing}))\n"
        "        except Exception as _e:\n"
        "            _rb = False\n"
        "            if _new is not None:\n"
        "                try:\n"
        "                    _new.Delete()\n"
        "                    _rb = True\n"
        "                except Exception:\n"
        "                    pass\n"
        "            print(json.dumps({'ok': False, 'created': False, 'error': str(_e), 'rolled_back': _rb}))\n"
    )


# ---------------------------------------------------------------------------
# Point mass conversion
# ---------------------------------------------------------------------------

_LABEL_BAD = re.compile(r"[\s\\/\"'<>|:*?]+")


def ns_label(text: str) -> str:
    """A name fragment for the attachment-face named selection: a readable start of the prefix plus
    a hash of the WHOLE prefix, so two prefixes (SYS\\B, SYS\\Backing, or two that share their first
    characters) never share a label, and the duplicate-point-mass check can match it exactly."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return _LABEL_BAD.sub("_", text)[:24] + "_" + digest


def prefix_match(prefix: str) -> str:
    if not prefix.strip():
        raise ValueError(
            "body_name_prefix is required: an empty prefix would match every body and replace the whole model"
        )
    return (
        "_PREFIX = " + lit(prefix) + "\n"
        "matched = [b for b in all_bodies if str(b.Name).startswith(_PREFIX) and not b.Suppressed]\n"
    ), "No unsuppressed body name starts with the prefix"


def part_match(part: str):
    if not part.strip():
        raise ValueError("part_name is required")
    return (
        "_PART = " + lit(part) + "\n"
        "matched = [b for b in all_bodies if str(b.Parent.Name) == _PART and not b.Suppressed]\n"
    ), "No unsuppressed bodies in that part"


# Objects (loads, results, contacts, mesh controls) scoped to geometry of the bodies that a
# point-mass conversion suppresses: they would silently be left with an empty scope.
SCOPE_LOSS_DEFS = (
    "def _body_entity_ids(_bodies):\n"
    "    _ids = set()\n"
    "    for _b in _bodies:\n"
    "        _gb = _b.GetGeoBody()\n"
    "        _ids.add(int(_gb.Id))\n"
    "        for _a in ('Faces', 'Edges', 'Vertices'):\n"
    "            for _e in getattr(_gb, _a):\n"
    "                _ids.add(int(_e.Id))\n"
    "    return _ids\n"
    "def _scope_loss(_bodies):\n"
    "    _ids = _body_entity_ids(_bodies)\n"
    "    _m = ExtAPI.DataModel.Project.Model\n"
    "    _c = []\n"
    "    for _an in _m.Analyses:\n"
    "        for _o in list(_an.Children):\n"
    "            _c.append((str(_an.Name), _o))\n"
    "        try:\n"
    "            for _o in list(_an.Solution.Children):\n"
    "                _c.append((str(_an.Name), _o))\n"
    "        except Exception:\n"
    "            pass\n"
    "    for _o in _m.GetChildren(DataModelObjectCategory.ContactRegion, True):\n"
    "        _c.append(('Connections', _o))\n"
    "    try:\n"
    "        for _o in list(_m.Mesh.Children):\n"
    "            _c.append(('Mesh', _o))\n"
    "    except Exception:\n"
    "        pass\n"
    "    _out = []\n"
    "    for _w, _o in _c:\n"
    "        _hit = False\n"
    "        for _a in ('Location', 'GeometryLocation', 'SourceLocation', 'TargetLocation'):\n"
    "            try:\n"
    "                _L = getattr(_o, _a)\n"
    "                if _L is not None and (set([int(_i) for _i in _L.Ids]) & _ids):\n"
    "                    _hit = True\n"
    "            except Exception:\n"
    "                pass\n"
    "        if _hit:\n"
    "            _out.append({'name': str(_o.Name), 'type': str(_o.GetType().Name), 'in': _w})\n"
    "    return _out\n"
)


def point_mass_script(
    match_lines: str,
    no_match_error: str,
    label: str,
    prox_mult: float,
    allow_duplicate: bool = False,
    acknowledge_scope_loss: bool = False,
) -> str:
    """Convert the matched bodies to one Point Mass scoped to the nearest coplanar faces.

    Everything is measured first with the model untouched; only then are the Point Mass and the
    named selection created and the bodies suppressed, and any failure undoes all of it.
    """
    prox = num("proximity_multiplier", prox_mult, lo=0.1, hi=100.0)
    if not isinstance(allow_duplicate, bool) or not isinstance(acknowledge_scope_loss, bool):
        raise ValueError("allow_duplicate and acknowledge_scope_loss must be true or false")
    return (
        "import math, json\n" + SCOPE_LOSS_DEFS + "model = ExtAPI.DataModel.Project.Model\n"
        "_ALLOW_DUP = " + ("True" if allow_duplicate else "False") + "\n"
        "_ACK = " + ("True" if acknowledge_scope_loss else "False") + "\n"
        "_BASE = u'NearestFace_' + " + lit(label) + "\n"
        "_TO_M = {'m': 1.0, 'mm': 0.001, 'cm': 0.01, 'um': 1e-6, 'in': 0.0254, 'ft': 0.3048}\n"
        "_gu = str(ExtAPI.DataModel.GeoData.Unit)\n"
        "if _gu not in _TO_M:\n"
        "    raise Exception('unsupported geometry length unit: ' + _gu)\n"
        "_G2M = _TO_M[_gu]\n"
        "all_bodies = model.Geometry.GetChildren(DataModelObjectCategory.Body, True)\n"
        + match_lines
        + "_solids = [b for b in matched if 'Solid' in str(b.GetGeoBody().BodyType)]\n"
        "_solid_ids = set([int(b.GetGeoBody().Id) for b in _solids])\n"
        "_skipped_names = [str(b.Name) for b in matched if int(b.GetGeoBody().Id) not in _solid_ids]\n"
        "_lost = _scope_loss(_solids) if _solids else []\n"
        "_dup_pm = None\n"
        "if not _ALLOW_DUP:\n"
        "    for _x in model.Geometry.GetChildren(DataModelObjectCategory.PointMass, True):\n"
        "        try:\n"
        "            _nm = str(_x.Location.Name)\n"
        "            if _nm == _BASE or (_nm.startswith(_BASE + u'_') and _nm[len(_BASE) + 1:].isdigit()):\n"
        "                _dup_pm = str(_x.Name)\n"
        "                break\n"
        "        except Exception:\n"
        "            pass\n"
        "if not matched and _dup_pm is not None:\n"
        "    print(json.dumps({'ok': False, 'error': u'Already converted: point mass ' + _dup_pm + u' is scoped to a "
        "selection named ' + _BASE + u'... for this exact prefix/part, and its bodies are suppressed. Delete the point "
        "mass and its selection and unsuppress the bodies to convert again', 'existing_point_mass': _dup_pm}))\n"
        "elif not matched:\n"
        "    print(json.dumps({'ok': False, 'error': " + lit(no_match_error) + "}))\n"
        "elif not _solids:\n"
        "    print(json.dumps({'ok': False, 'error': 'No solid bodies found in the match'}))\n"
        "elif _lost and not _ACK:\n"
        "    print(json.dumps({'ok': False, 'error': str(len(_lost)) + u' object(s) are scoped to geometry of the bodies "
        "that would be suppressed and would be left with an empty scope; nothing was changed. Re-scope them first, or "
        "pass acknowledge_scope_loss=true', 'scope_loss': _lost[:50]}))\n"
        "elif _dup_pm is not None:\n"
        "    print(json.dumps({'ok': False, 'error': u'A point mass (' + _dup_pm + u') scoped to a selection named ' + _BASE + "
        "u'... was already made for this exact prefix/part; converting again would count the same mass twice. Delete it "
        "(and the selection) first, or pass allow_duplicate=true', 'existing_point_mass': _dup_pm}))\n"
        "else:\n"
        "    _matched_ids = set([int(b.GetGeoBody().Id) for b in matched])\n"
        "    # --- measure (nothing in the model changes yet) ---\n"
        "    eq_min = [1e18, 1e18, 1e18]\n"
        "    eq_max = [-1e18, -1e18, -1e18]\n"
        "    for _b in matched:\n"
        "        for _f in _b.GetGeoBody().Faces:\n"
        "            try:\n"
        "                _c = _f.Centroid\n"
        "                for _i in range(3):\n"
        "                    _v = _c[_i] * _G2M\n"
        "                    if _v < eq_min[_i]: eq_min[_i] = _v\n"
        "                    if _v > eq_max[_i]: eq_max[_i] = _v\n"
        "            except Exception:\n"
        "                pass\n"
        "    _PM_mass = Quantity('0 [kg]')\n"
        "    _PM_x = Quantity('0 [m]')\n"
        "    _PM_y = Quantity('0 [m]')\n"
        "    _PM_z = Quantity('0 [m]')\n"
        "    _PM_Ixx = Quantity('0 [kg m m]')\n"
        "    _PM_Iyy = Quantity('0 [kg m m]')\n"
        "    _PM_Izz = Quantity('0 [kg m m]')\n"
        "    _PM_vol = 0.0\n"
        "    _len_unit = _solids[0].CentroidX.Unit\n"
        "    _mass_unit = _solids[0].Mass.Unit\n"
        "    for _body in _solids:\n"
        "        _bm = _body.Mass\n"
        "        _bx = _body.CentroidX\n"
        "        _by = _body.CentroidY\n"
        "        _bz = _body.CentroidZ\n"
        "        _bIx = _body.MomentOfInertiaIp1\n"
        "        _bIy = _body.MomentOfInertiaIp2\n"
        "        _bIz = _body.MomentOfInertiaIp3\n"
        "        _PM_vol += _body.Volume.ConvertUnit('m m m').Value\n"
        "        _nx = (_PM_x * _PM_mass + _bx * _bm) / (_PM_mass + _bm)\n"
        "        _ny = (_PM_y * _PM_mass + _by * _bm) / (_PM_mass + _bm)\n"
        "        _nz = (_PM_z * _PM_mass + _bz * _bm) / (_PM_mass + _bm)\n"
        "        _PM_Ixx = _PM_Ixx + _bIx + _bm*((_ny-_by)**2+(_nz-_bz)**2) + _PM_mass*((_ny-_PM_y)**2+(_nz-_PM_z)**2)\n"
        "        _PM_Iyy = _PM_Iyy + _bIy + _bm*((_nx-_bx)**2+(_nz-_bz)**2) + _PM_mass*((_nx-_PM_x)**2+(_nz-_PM_z)**2)\n"
        "        _PM_Izz = _PM_Izz + _bIz + _bm*((_nx-_bx)**2+(_ny-_by)**2) + _PM_mass*((_nx-_PM_x)**2+(_ny-_PM_y)**2)\n"
        "        _PM_mass = _PM_mass + _bm\n"
        "        _PM_x = _nx; _PM_y = _ny; _PM_z = _nz\n"
        "    _warn = []\n"
        "    _warn.append(u'inertia: Mechanical exposes only the PRINCIPAL moments of each body (MomentOfInertiaIp1-3); they "
        "are used as Ixx, Iyy, Izz, which is exact only when the principal axes are the global axes')\n"
        "    if len(_solids) > 1:\n"
        "        _warn.append(u'products of inertia are dropped: a point mass takes only Ixx, Iyy, Izz')\n"
        "    if _skipped_names:\n"
        "        _warn.append(u'matched bodies that are not solids were neither converted nor suppressed: ' + "
        "u', '.join(_skipped_names))\n"
        "    if _lost:\n"
        "        _warn.append(str(len(_lost)) + u' object(s) lose their scope because their bodies are suppressed "
        "(acknowledged)')\n"
        "    _mis = 0\n"
        "    for _b in _solids:\n"
        "        for _f in _b.GetGeoBody().Faces:\n"
        "            try:\n"
        "                if 'Plane' not in str(_f.SurfaceType):\n"
        "                    continue\n"
        "                _nv = _f.NormalAtParam(0.5, 0.5)\n"
        "                if max(abs(_nv[0]), abs(_nv[1]), abs(_nv[2])) < 0.999:\n"
        "                    _mis += 1\n"
        "            except Exception:\n"
        "                pass\n"
        "    if _mis:\n"
        "        _warn.append(str(_mis) + u' planar face(s) are not aligned with the global axes: the principal axes are "
        "probably not the global axes, so Ixx/Iyy/Izz may be wrong')\n"
        "    if _PM_mass.Value <= 0:\n"
        "        print(json.dumps({'ok': False, 'error': 'The matched solid bodies have no mass'}))\n"
        "    else:\n"
        "        cx = _PM_x.ConvertUnit('m').Value\n"
        "        cy = _PM_y.ConvertUnit('m').Value\n"
        "        cz = _PM_z.ConvertUnit('m').Value\n"
        "        _pinball = (3.0 * _PM_vol / (4.0 * math.pi)) ** (1.0 / 3.0)\n"
        "        _search_r = _pinball * " + fnum(prox) + "\n"
        "        _search_ftol = _pinball * 0.4\n"
        "        _SEED_TOL = 0.005\n"
        "        _PLANE_TOL = 0.005\n"
        "        _FP_TOL = 0.020\n"
        "        def _d2box_exact(c):\n"
        "            dx = max(eq_min[0]-c[0], 0.0, c[0]-eq_max[0])\n"
        "            dy = max(eq_min[1]-c[1], 0.0, c[1]-eq_max[1])\n"
        "            dz = max(eq_min[2]-c[2], 0.0, c[2]-eq_max[2])\n"
        "            return math.sqrt(dx*dx+dy*dy+dz*dz)\n"
        "        def _d2box_exp(c):\n"
        "            dx = max(eq_min[0]-_search_ftol-c[0], 0.0, c[0]-eq_max[0]-_search_ftol)\n"
        "            dy = max(eq_min[1]-_search_ftol-c[1], 0.0, c[1]-eq_max[1]-_search_ftol)\n"
        "            dz = max(eq_min[2]-_search_ftol-c[2], 0.0, c[2]-eq_max[2]-_search_ftol)\n"
        "            return math.sqrt(dx*dx+dy*dy+dz*dz)\n"
        "        # structure = every unsuppressed body that is not part of the equipment\n"
        "        _structure = [b for b in all_bodies if not b.Suppressed and int(b.GetGeoBody().Id) not in _matched_ids]\n"
        "        _best_score = -1e18\n"
        "        _best_face = None\n"
        "        for _body in _structure:\n"
        "            for _face in _body.GetGeoBody().Faces:\n"
        "                try:\n"
        "                    _c0 = _face.Centroid\n"
        "                    c = [_c0[0]*_G2M, _c0[1]*_G2M, _c0[2]*_G2M]\n"
        "                    _dist_cg = math.sqrt((c[0]-cx)**2+(c[1]-cy)**2+(c[2]-cz)**2)\n"
        "                    if _dist_cg > _search_r: continue\n"
        "                    if _d2box_exp(c) > 0: continue\n"
        "                    if _d2box_exact(c) > _SEED_TOL: continue\n"
        "                    n = _face.NormalAtParam(0.5, 0.5)\n"
        "                    _dv = [cx-c[0], cy-c[1], cz-c[2]]\n"
        "                    if _dist_cg == 0: continue\n"
        "                    _align = (n[0]*_dv[0]+n[1]*_dv[1]+n[2]*_dv[2]) / _dist_cg\n"
        "                    if _align < 0.4: continue\n"
        "                    if _align > _best_score:\n"
        "                        _best_score = _align\n"
        "                        _best_face = (int(_face.Id), [c[0],c[1],c[2]], [n[0],n[1],n[2]])\n"
        "                except Exception:\n"
        "                    pass\n"
        "        if _best_face is None:\n"
        "            print(json.dumps({'ok': False, 'error': 'No attachment face found on equipment boundary; nothing was changed'}))\n"
        "        else:\n"
        "            _bf_id, _bf_c, _bf_n = _best_face\n"
        "            _bf_lvl = _bf_n[0]*_bf_c[0]+_bf_n[1]*_bf_c[1]+_bf_n[2]*_bf_c[2]\n"
        "            _abs_n = [abs(_bf_n[0]), abs(_bf_n[1]), abs(_bf_n[2])]\n"
        "            _dom_ax = 0\n"
        "            for _i in range(1, 3):\n"
        "                if _abs_n[_i] > _abs_n[_dom_ax]: _dom_ax = _i\n"
        "            _fp = [_i for _i in range(3) if _i != _dom_ax]\n"
        "            _attach_ids = []\n"
        "            for _body in _structure:\n"
        "                for _face in _body.GetGeoBody().Faces:\n"
        "                    try:\n"
        "                        _c0 = _face.Centroid\n"
        "                        c = [_c0[0]*_G2M, _c0[1]*_G2M, _c0[2]*_G2M]\n"
        "                        if math.sqrt((c[0]-cx)**2+(c[1]-cy)**2+(c[2]-cz)**2) > _search_r: continue\n"
        "                        if not (eq_min[_fp[0]]-_FP_TOL <= c[_fp[0]] <= eq_max[_fp[0]]+_FP_TOL): continue\n"
        "                        if not (eq_min[_fp[1]]-_FP_TOL <= c[_fp[1]] <= eq_max[_fp[1]]+_FP_TOL): continue\n"
        "                        n = _face.NormalAtParam(0.5, 0.5)\n"
        "                        if abs(n[0]*_bf_n[0]+n[1]*_bf_n[1]+n[2]*_bf_n[2]) < 0.9: continue\n"
        "                        _lvl = n[0]*c[0]+n[1]*c[1]+n[2]*c[2]\n"
        "                        if abs(_lvl - _bf_lvl) > _PLANE_TOL: continue\n"
        "                        _attach_ids.append(int(_face.Id))\n"
        "                    except Exception:\n"
        "                        pass\n"
        "            # --- change the model; undo everything if any step fails ---\n"
        "            _pm = None\n"
        "            _ns = None\n"
        "            _suppressed = []\n"
        "            try:\n"
        "                _existing = [str(_x.Name) for _x in model.GetChildren(DataModelObjectCategory.NamedSelection, True)]\n"
        "                _base = u'NearestFace_' + " + lit(label) + "\n"
        "                _ns_name = _base\n"
        "                _k = 2\n"
        "                while _ns_name in _existing:\n"
        "                    _ns_name = _base + u'_' + str(_k)\n"
        "                    _k += 1\n"
        "                _pm = model.Geometry.AddPointMass()\n"
        "                _pm.Name = u'%d bodies, %.5g %s' % (len(_solids), round(_PM_mass.ConvertUnit(_mass_unit).Value, 5), _mass_unit)\n"
        "                _pm.Mass = _PM_mass\n"
        "                _pm.InternalObject.LocationX = _PM_x.ConvertUnit(_len_unit).Value\n"
        "                _pm.InternalObject.LocationY = _PM_y.ConvertUnit(_len_unit).Value\n"
        "                _pm.InternalObject.LocationZ = _PM_z.ConvertUnit(_len_unit).Value\n"
        "                _pm.MassMomentOfInertiaX = _PM_Ixx\n"
        "                _pm.MassMomentOfInertiaY = _PM_Iyy\n"
        "                _pm.MassMomentOfInertiaZ = _PM_Izz\n"
        "                _pm.Activate()\n"
        "                _ns_sel = ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)\n"
        "                _ns_sel.Ids = _attach_ids\n"
        "                _ns = model.AddNamedSelection()\n"
        "                _ns.Name = _ns_name\n"
        "                _ns.Location = _ns_sel\n"
        "                _pm.Location = _ns\n"
        "                _pm.PinballRegion = Quantity('0 [mm]')\n"
        "                for _b in _solids:\n"
        "                    _b.Suppressed = True\n"
        "                    _suppressed.append(_b)\n"
        "                print(json.dumps({'ok': True, 'cg_mm': [round(cx*1000,1), round(cy*1000,1), round(cz*1000,1)],\n"
        "                    'pinball': '0 [mm] (All)', 'bodies_matched': len(matched), 'bodies_suppressed': len(_suppressed),\n"
        "                    'mass': str(_PM_mass), 'attachment_faces': len(_attach_ids), 'named_selection': str(_ns.Name),\n"
        "                    'point_mass': str(_pm.Name), 'inertia_basis': 'principal', 'warnings': _warn, "
        "'skipped_non_solid_bodies': _skipped_names, 'scope_loss': _lost[:50]}))\n"
        "            except Exception as _e:\n"
        "                for _b in _suppressed:\n"
        "                    try:\n"
        "                        _b.Suppressed = False\n"
        "                    except Exception:\n"
        "                        pass\n"
        "                for _o in (_ns, _pm):\n"
        "                    if _o is not None:\n"
        "                        try:\n"
        "                            _o.Delete()\n"
        "                        except Exception:\n"
        "                            pass\n"
        "                print(json.dumps({'ok': False, 'error': str(_e), 'rolled_back': True}))\n"
    )


# ---------------------------------------------------------------------------
# Result export
# ---------------------------------------------------------------------------

#: Result types whose values are never negative, so a through-thickness linearization of them is
#: not meaningful (membrane + bending of an absolute value is non-conservative).
#: Result types known to be signed (they can change sign through the thickness, so a membrane +
#: bending split means something). Anything else -- von Mises, stress intensity, MaximumShearStress,
#: heat flux magnitudes, safety factors, ... -- is treated as unsigned/unknown. Exact type names:
#: "MaximumShearStress" must not match "ShearStress".
SIGNED_TYPES = (
    "NormalStress",
    "ShearStress",
    "MaximumPrincipalStress",
    "MiddlePrincipalStress",
    "MinimumPrincipalStress",
    "DirectionalDeformation",
    "DirectionalVelocity",
    "DirectionalAcceleration",
    "NormalElasticStrain",
    "ShearElasticStrain",
    "MaximumPrincipalElasticStrain",
    "MiddlePrincipalElasticStrain",
    "MinimumPrincipalElasticStrain",
    "DirectionalHeatFlux",
)


# os.path.islink is False for every symbolic link and junction in Mechanical's IronPython (seen
# live); the reparse-point attribute is the reliable test. CPython (the unit tests) has no
# System module and falls back to islink.
LINK_DEFS = (
    "try:\n"
    "    import System\n"
    "except ImportError:\n"
    "    System = None\n"
    "def _is_link(_p):\n"
    "    if System is not None:\n"
    "        try:\n"
    "            return (int(System.IO.File.GetAttributes(_p)) & 0x400) != 0\n"
    "        except Exception:\n"
    "            return False\n"
    "    return os.path.islink(_p)\n"
)


def export_nodal_script(
    result_name: str,
    output_path: str,
    analysis_index: int = 0,
    overwrite: bool = False,
    max_nodes: int = 500_000,
    time_budget_s: float = 120.0,
    load_step: int = 0,
) -> str:
    """Write nodal id, coordinates (mm) and values of an evaluated result to a JSON file.

    output_path is expected to be already validated by the server (allowed folder, .json), but the
    script enforces the overwrite policy itself: it refuses an existing file unless overwrite is
    true, never follows or replaces a link, writes through a uniquely named exclusive temporary
    file and only ever removes a file it created. It refuses an unsolved model and states the
    units: coordinates in mm; values in the result's unit, with value_unit_verified saying
    whether the largest exported value agrees with the result's own Maximum (PlotData does not
    declare its unit).
    """
    check_text("result_name", result_name)
    check_text("output_path", output_path)
    if not result_name.strip() or not output_path.strip():
        raise ValueError("result_name and output_path are required")
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite must be true or false")
    idx = index(analysis_index)
    max_nodes = integer("max_nodes", max_nodes, 1, 100_000_000)
    load_step = integer("load_step", load_step, 0, 100_000)
    time_budget_s = num("time_budget_s", time_budget_s, positive=True, hi=86400.0)
    return (
        "import json, os, time\n"
        + LINK_DEFS
        + pick_analysis(idx)
        + "_RES = "
        + lit(result_name)
        + "\n"
        "_OUT = " + lit(output_path.replace("\\", "/")) + "\n"
        "_OVERWRITE = " + ("True" if overwrite else "False") + "\n"
        "_MAXN = " + str(max_nodes) + "\n"
        "_LOADSTEP = " + str(load_step) + "\n"
        "_DEADLINE = time.time() + " + fnum(time_budget_s) + "\n"
        "_SIGNED = " + repr(SIGNED_TYPES) + "\n"
        "_TO_MM = " + _TO_MM_SRC + "\n"
        "_sol = _analysis.Solution\n"
        "_status = str(_sol.Status)\n"
        "_r = None\n"
        "for _c in _sol.Children:\n"
        "    if str(_c.Name) == _RES:\n"
        "        _r = _c\n"
        "        break\n"
        "_tmp = None\n"
        "if _r is None:\n"
        "    print(json.dumps({'ok': False, 'error': u'Result not found: ' + _RES}))\n"
        "elif _status != 'Done':\n"
        "    print(json.dumps({'ok': False, 'error': 'the analysis is not solved yet (status ' + _status + "
        "'); solve it before exporting nodal results'}))\n"
        "elif _is_link(_OUT) or (os.path.exists(_OUT) and not os.path.isfile(_OUT)):\n"
        "    print(json.dumps({'ok': False, 'error': u'Refusing to replace a link or a non-file: ' + _OUT}))\n"
        "elif os.path.exists(_OUT) and not _OVERWRITE:\n"
        "    print(json.dumps({'ok': False, 'error': u'File exists: ' + _OUT + u' (pass overwrite=true to replace it)'}))\n"
        "else:\n"
        "    try:\n"
        "        _nsteps = 1\n"
        "        try:\n"
        "            _nsteps = int(_analysis.AnalysisSettings.NumberOfSteps)\n"
        "        except Exception:\n"
        "            pass\n"
        "        if _LOADSTEP > 0:\n"
        "            _r.LoadStep = _LOADSTEP\n"
        "        elif _nsteps > 1:\n"
        "            raise Exception('this analysis has ' + str(_nsteps) + ' load steps; pass load_step so the exported "
        "step is explicit')\n"
        "        _step = {'number_of_steps': _nsteps}\n"
        "        for _a in ('LoadStep', 'Substep', 'DisplayTime', 'SetNumber'):\n"
        "            try:\n"
        "                _step[_a] = str(getattr(_r, _a))\n"
        "            except Exception:\n"
        "                pass\n"
        "        _r.EvaluateAllResults()\n"
        "        _pd = _r.PlotData\n"
        "        _nodes = [int(n) for n in _pd['Node']]\n"
        "        _vals = [float(v) for v in _pd['Values']]\n"
        "        if len(_nodes) > _MAXN:\n"
        "            raise Exception('the result has ' + str(len(_nodes)) + ' nodes; the export limit is ' + str(_MAXN) "
        "+ ' (raise MECHANICAL_MCP_MAX_EXPORT_NODES)')\n"
        "        _mesh = ExtAPI.DataModel.MeshDataByName('Global')\n"
        "        _mu = str(_mesh.Unit)\n"
        "        if _mu not in _TO_MM:\n"
        "            raise Exception('unsupported mesh length unit: ' + _mu)\n"
        "        _k = _TO_MM[_mu]\n"
        "        _xyz = []\n"
        "        for _n in _nodes:\n"
        "            if time.time() > _DEADLINE:\n"
        "                raise Exception('the export did not finish within its time budget; nothing was written')\n"
        "            _nd = _mesh.NodeById(_n)\n"
        "            _xyz.append([_nd.X * _k, _nd.Y * _k, _nd.Z * _k])\n"
        "        _vu = 'unknown'\n"
        "        _verified = False\n"
        "        _ref = None\n"
        "        try:\n"
        "            _q = _r.Maximum\n"
        "            _vu = str(_q.Unit)\n"
        "            _ref = float(_q.Value)\n"
        "            _top = max(_vals)\n"
        "            _verified = abs(_top - _ref) <= 1e-3 * max(abs(_top), abs(_ref), 1e-30)\n"
        "        except Exception:\n"
        "            pass\n"
        "        _ori = ''\n"
        "        try:\n"
        "            _ori = str(_r.NormalOrientation)\n"
        "        except Exception:\n"
        "            pass\n"
        "        _tn = str(_r.GetType().Name)\n"
        "        _signed = _tn in _SIGNED\n"
        "        _doc = {'result': _RES, 'result_type': _tn, 'signed': _signed, 'orientation': _ori, "
        "'step': _step, 'node': _nodes, 'xyz_mm': _xyz, 'coordinate_unit': 'mm', 'values': _vals, 'value_unit': _vu, "
        "'value_unit_verified': _verified}\n"
        "        _tmp = _OUT + '.' + str(os.getpid()) + '_' + str(int(time.time() * 1000)) + '.part'\n"
        "        _fd = os.open(_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)\n"
        "        _f = os.fdopen(_fd, 'w')\n"
        "        try:\n"
        "            json.dump(_doc, _f)\n"
        "        finally:\n"
        "            _f.close()\n"
        "        if os.path.exists(_OUT) or _is_link(_OUT):\n"
        "            # the file appeared (or is being replaced on request) after the first check\n"
        "            if not _OVERWRITE:\n"
        "                raise Exception(u'File appeared while exporting: ' + _OUT + u' (not replaced; pass overwrite=true)')\n"
        "            if _is_link(_OUT) or not os.path.isfile(_OUT):\n"
        "                raise Exception(u'Refusing to replace a link or a non-file: ' + _OUT)\n"
        "            os.remove(_OUT)\n"
        "        os.rename(_tmp, _OUT)\n"
        "        _tmp = None\n"
        "        print(json.dumps({'ok': True, 'file': _OUT, 'nodes': len(_nodes), 'coordinate_unit': 'mm', "
        "'value_unit': _vu, 'value_unit_verified': _verified, 'max_exported': max(_vals), 'result_maximum': _ref, "
        "'orientation': _ori, 'signed': _signed, 'step': _step}))\n"
        "    except Exception as _e:\n"
        "        if _tmp is not None:\n"
        "            try:\n"
        "                if os.path.exists(_tmp):\n"
        "                    os.remove(_tmp)\n"
        "            except Exception:\n"
        "                pass\n"
        "        print(json.dumps({'ok': False, 'error': str(_e)}))\n"
    )


# ---------------------------------------------------------------------------
# Listing scripts (read only)
# ---------------------------------------------------------------------------

#: Methods whose Add* objects are not loads or supports.
_NOT_LOADS = (
    "Comment",
    "Figure",
    "Image",
    "CommandSnippet",
    "PythonCodeEventBased",
    "GeometryBasedAdaptivity",
    "ContactStepControl",
    "NonlinearAdaptiveRegion",
)

# _list_loads(analysis): the loads and supports of an analysis with scope and key values. The
# type allow-list is read from the analysis itself (its Add* methods), because real type names
# differ ('ANSYSAnalysisSettings' for static, 'DataModelObject' for explicit settings).
LOADS_DEFS = (
    SCOPE_TYPE_DEFS + "_NOT_LOADS = " + repr(_NOT_LOADS) + "\n"
    "_VAL_ATTRS = ('DefineBy', 'Magnitude', 'XComponent', 'YComponent', 'ZComponent', "
    "'RotationX', 'RotationY', 'RotationZ', 'Direction')\n"
    "def _list_loads(_an):\n"
    "    _types = set()\n"
    "    for _d in dir(_an):\n"
    "        if _d.startswith('Add') and _d[3:] not in _NOT_LOADS:\n"
    "            _types.add(_d[3:])\n"
    "    _out = []\n"
    "    for _c in _an.Children:\n"
    "        _t = str(_c.GetType().Name)\n"
    "        if _t not in _types:\n"
    "            continue\n"
    "        _row = {'name': str(_c.Name), 'type': _t}\n"
    "        try:\n"
    "            _row['suppressed'] = bool(_c.Suppressed)\n"
    "        except Exception:\n"
    "            pass\n"
    "        try:\n"
    "            _row['object_state'] = str(_c.ObjectState)\n"
    "        except Exception:\n"
    "            pass\n"
    "        for _a in ('Location', 'GeometryLocation'):\n"
    "            try:\n"
    "                _L = getattr(_c, _a)\n"
    "            except Exception:\n"
    "                continue\n"
    "            if _L is None:\n"
    "                continue\n"
    "            try:\n"
    "                _row['scope_named_selection'] = str(_L.Name)\n"
    "            except Exception:\n"
    "                pass\n"
    "            try:\n"
    "                _row['scope_entities'] = len(_L.Ids)\n"
    "            except Exception:\n"
    "                pass\n"
    "            _row['scope_type'] = _scope_type(_L)\n"
    "            break\n"
    "        else:\n"
    "            _row['scope'] = 'not scoped (Mechanical holds no Location)'\n"
    "        _vals = {}\n"
    "        for _a in _VAL_ATTRS:\n"
    "            try:\n"
    "                _vals[_a] = str(getattr(_c, _a))\n"
    "            except Exception:\n"
    "                pass\n"
    "        if _vals:\n"
    "            _row['values'] = _vals\n"
    "        _out.append(_row)\n"
    "    return _out\n"
)


def list_loads_script(idx: int) -> str:
    return (
        "import json\n"
        + pick_analysis(idx)
        + LOADS_DEFS
        + "print(json.dumps({'ok': True, 'analysis': str(_analysis.Name), "
        "'analysis_type': str(_analysis.AnalysisType), 'boundary_conditions': _list_loads(_analysis)}))\n"
    )


def list_contact_regions_script() -> str:
    return (
        "import json\n"
        "_m = ExtAPI.DataModel.Project.Model\n"
        "_geo = ExtAPI.DataModel.GeoData\n"
        "def _bodies_of(_L):\n"
        "    _names = []\n"
        "    try:\n"
        "        for _i in _L.Ids:\n"
        "            _e = _geo.GeoEntityById(int(_i))\n"
        "            _n = str(_e.Body.Name)\n"
        "            if _n not in _names:\n"
        "                _names.append(_n)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return _names\n"
        "_out = []\n"
        "for _i, _c in enumerate(_m.GetChildren(DataModelObjectCategory.ContactRegion, True)):\n"
        "    _row = {'index': _i, 'name': str(_c.Name), 'contact_type': str(_c.ContactType)}\n"
        "    try:\n"
        "        _row['suppressed'] = bool(_c.Suppressed)\n"
        "    except Exception:\n"
        "        pass\n"
        "    for _k, _a in (('source', 'SourceLocation'), ('target', 'TargetLocation')):\n"
        "        try:\n"
        "            _L = getattr(_c, _a)\n"
        "            _row[_k + '_entities'] = len(_L.Ids)\n"
        "            _row[_k + '_bodies'] = _bodies_of(_L)\n"
        "        except Exception:\n"
        "            pass\n"
        "    try:\n"
        "        _row['friction_coefficient'] = float(_c.FrictionCoefficient)\n"
        "    except Exception:\n"
        "        pass\n"
        "    _out.append(_row)\n"
        "_names = [_r['name'] for _r in _out]\n"
        "_dups = sorted(set([_n for _n in _names if _names.count(_n) > 1]))\n"
        "print(json.dumps({'ok': True, 'contact_regions': _out, 'duplicate_names': _dups}))\n"
    )


_NOT_SOLVED_LIST = "not solved yet: values are null until the analysis is solved"

_RESULT_ROW = (
    "def _result_row(_r, _solved):\n"
    "    _row = {'name': str(_r.Name), 'type': str(_r.GetType().Name)}\n"
    "    for _a in ('Location', 'GeometryLocation'):\n"
    "        try:\n"
    "            _L = getattr(_r, _a)\n"
    "        except Exception:\n"
    "            continue\n"
    "        if _L is None:\n"
    "            continue\n"
    "        try:\n"
    "            _row['scope_named_selection'] = str(_L.Name)\n"
    "        except Exception:\n"
    "            pass\n"
    "        try:\n"
    "            _row['scope_entities'] = len(_L.Ids)\n"
    "        except Exception:\n"
    "            pass\n"
    "        break\n"
    "    for _a, _kinds in (('NormalOrientation', ('Normal', 'Directional')), ('Mode', ('TotalDeformation',))):\n"
    "        if not any([_k in _row['type'] for _k in _kinds]):\n"
    "            continue\n"
    "        try:\n"
    "            _row[_a] = str(getattr(_r, _a))\n"
    "        except Exception:\n"
    "            pass\n"
    "    if ('scope_entities' not in _row and 'scope_named_selection' not in _row) or "
    "(_row.get('scope_entities') == 0 and not _row.get('scope_named_selection')):\n"
    "        _row['scope'] = 'whole model'\n"
    "        _row.pop('scope_entities', None)\n"
    "        _row.pop('scope_named_selection', None)\n"
    "    _row['max'] = None\n"
    "    _row['min'] = None\n"
    "    if _solved:\n"
    "        for _k, _a in (('max', 'Maximum'), ('min', 'Minimum')):\n"
    "            try:\n"
    "                _q = getattr(_r, _a)\n"
    "                _row[_k] = str(_q)\n"
    "                _row[_k + '_value'] = float(_q.Value)\n"
    "                _row['unit'] = str(_q.Unit)\n"
    "            except Exception:\n"
    "                pass\n"
    "    return _row\n"
)


def list_results_script(idx: int) -> str:
    return (
        "import json\n" + pick_analysis(idx) + _RESULT_ROW + "_sol = _analysis.Solution\n"
        "_status = str(_sol.Status)\n"
        "_solved = _status == 'Done'\n"
        "_rows = []\n"
        "for _r in _sol.Children:\n"
        "    if str(_r.Name) == 'Solution Information':\n"
        "        continue\n"
        "    _rows.append(_result_row(_r, _solved))\n"
        "print(json.dumps({'ok': True, 'solution_status': _status, 'solved': _solved, 'results': _rows, "
        "'note': (None if _solved else " + lit(_NOT_SOLVED_LIST) + ")}))\n"
    )


def get_result_script(idx: int, result_name: str) -> str:
    check_text("result_name", result_name)
    if not result_name.strip():
        raise ValueError("result_name is required")
    return (
        "import json\n" + pick_analysis(idx) + _RESULT_ROW + "_RES = " + lit(result_name) + "\n"
        "_sol = _analysis.Solution\n"
        "_status = str(_sol.Status)\n"
        "_solved = _status == 'Done'\n"
        "_hits = [_r for _r in _sol.Children if str(_r.Name) == _RES]\n"
        "if not _hits:\n"
        "    print(json.dumps({'ok': False, 'error': u'Result not found: ' + _RES}))\n"
        "else:\n"
        "    _row = _result_row(_hits[0], _solved)\n"
        "    if _solved:\n"
        "        for _k, _a in (('x', 'XAxis'), ('y', 'YAxis'), ('z', 'ZAxis'), ('total', 'Total')):\n"
        "            try:\n"
        "                _row[_k] = str(getattr(_hits[0], _a))\n"
        "            except Exception:\n"
        "                pass\n"
        "    _row.update({'ok': True, 'solution_status': _status, 'solved': _solved, "
        "'duplicates': len(_hits) - 1, 'note': (None if _solved else "
        + lit(_NOT_SOLVED_LIST)
        + ")})\n"
        "    print(json.dumps(_row))\n"
    )
