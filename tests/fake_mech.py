"""A small fake of the Mechanical object model, enough to run the generated scripts under CPython.

The scripts are written to be valid in IronPython 2.7 and CPython; here they run against plain
Python objects, so rollback, status gating and validation logic is exercised without Mechanical.
Reading an attribute that was never set raises AttributeError, which catches typos in the scripts.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stdout

_L, _M, _T, _A = (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)
#: unit name -> (factor to the SI base unit, dimension vector over (length, mass, time, angle))
_UNITS = {
    "m": (1.0, _L),
    "mm": (1e-3, _L),
    "cm": (1e-2, _L),
    "um": (1e-6, _L),
    "in": (0.0254, _L),
    "ft": (0.3048, _L),
    "kg": (1.0, _M),
    "g": (1e-3, _M),
    "lbm": (0.45359237, _M),
    "s": (1.0, _T),
    "N": (1.0, (1, 1, -2, 0)),
    "Pa": (1.0, (-1, 1, -2, 0)),
    "Hz": (1.0, (0, 0, -1, 0)),
    "deg": (0.017453292519943295, _A),
}


def _parse_unit(unit):
    """'kg m m' / 'm s^-2' -> (factor, dims). Unknown names are their own dimensionless unit."""
    factor, dims = 1.0, (0, 0, 0, 0)
    for tok in unit.replace("[", " ").replace("]", " ").split():
        name, _, exp = tok.partition("^")
        e = int(exp) if exp else 1
        f, d = _UNITS.get(name, (1.0, (0, 0, 0, 0)))
        factor *= f**e
        dims = tuple(x + e * y for x, y in zip(dims, d))
    return factor, dims


class Q:
    """Quantity stand-in with real unit semantics: it stores an SI value and a dimension vector.

    Adding quantities of different dimension, or converting to a unit of another dimension,
    raises, like Mechanical does. `.Value` is the number in the quantity's own unit; derived
    quantities (products, quotients, powers) are reported in SI base units until converted
    with ConvertUnit."""

    def __init__(self, value, unit=None, _si=None, _dims=None):
        if _si is not None:
            self.si, self.dims, self.unit = _si, _dims, unit
            return
        if unit is None and isinstance(value, str):
            m = re.match(r"\s*([-+0-9.eE]+)\s*\[([^\]]*)\]", value)
            value, unit = float(m.group(1)), m.group(2)
        factor, dims = _parse_unit(unit or "m")
        self.si, self.dims, self.unit = float(value) * factor, dims, unit or "m"

    @property
    def Value(self):
        if self.unit is None:
            return self.si
        return self.si / _parse_unit(self.unit)[0]

    @property
    def Unit(self):
        return self.unit or "SI"

    def _o(self, o):
        if isinstance(o, Q):
            return o.si, o.dims
        return float(o), (0, 0, 0, 0)

    def _same(self, o):
        si, dims = self._o(o)
        if dims != self.dims:
            raise ValueError("incompatible units: %s vs %s" % (self.dims, dims))
        return si

    def __add__(self, o):
        return Q(None, self.unit, _si=self.si + self._same(o), _dims=self.dims)

    __radd__ = __add__

    def __sub__(self, o):
        return Q(None, self.unit, _si=self.si - self._same(o), _dims=self.dims)

    def __rsub__(self, o):
        return Q(None, self.unit, _si=self._same(o) - self.si, _dims=self.dims)

    def __mul__(self, o):
        si, dims = self._o(o)
        keep = self.unit if not isinstance(o, Q) else None
        return Q(None, keep, _si=self.si * si, _dims=tuple(a + b for a, b in zip(self.dims, dims)))

    __rmul__ = __mul__

    def __truediv__(self, o):
        si, dims = self._o(o)
        keep = self.unit if not isinstance(o, Q) else None
        return Q(None, keep, _si=self.si / si, _dims=tuple(a - b for a, b in zip(self.dims, dims)))

    def __pow__(self, p):
        return Q(None, None, _si=self.si**p, _dims=tuple(a * p for a in self.dims))

    def ConvertUnit(self, unit):
        factor, dims = _parse_unit(unit)
        if dims != self.dims:
            raise ValueError("cannot convert %s to %s" % (self.unit, unit))
        return Q(None, unit, _si=self.si, _dims=self.dims)

    def __str__(self):
        return "%g [%s]" % (self.Value, self.Unit)

    __repr__ = __str__


_OBJ_IDS = [100]
#: geometry entity id -> 'body' | 'face' | 'edge' | 'vertex' (filled by Fake)
_KINDS: dict = {}


def kinds_of(sel):
    ids = getattr(sel, "Ids", None) or []
    return {_KINDS.get(int(i), "face") for i in ids}


_SCOPE_KEYS = ("Location", "GeometryLocation", "SourceLocation", "TargetLocation")


class NSObj:
    """Marker mixin, see Obj.__setattr__."""


class Obj:
    """Attribute bag; Delete() removes it from its owner's children list."""

    def __init__(self, name="", owner=None, **attrs):
        object.__setattr__(self, "_fail", {})
        _OBJ_IDS[0] += 1
        object.__setattr__(self, "ObjectId", _OBJ_IDS[0])
        self.Name = name
        self._owner = owner
        self.Suppressed = False
        for k, v in attrs.items():
            setattr(self, k, v)

    def __setattr__(self, k, v):
        fail = object.__getattribute__(self, "_fail")
        if k in fail and fail[k] is not None:
            raise fail[k]
        if k == "Name" and isinstance(v, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise RuntimeError(
                "Unable to translate Unicode character \\uD800 at index %d to specified code page."
                % next(i for i, c in enumerate(v) if 0xD800 <= ord(c) <= 0xDFFF)
            )
        if k == "GeometryLocation" and isinstance(v, Obj) and "Location" in v.__dict__:
            # Mechanical hands back a selection (with the ids) when a named selection is assigned
            sel = Sel()
            sel.Ids = list(v.Location.Ids) if v.Location is not None else []
            sel.Name = v.Name
            v = sel
        if k in ("Location", "SourceLocation", "TargetLocation") and isinstance(v, NSObj):
            pass  # Mechanical hands the named selection itself back for loads and contacts
        object.__setattr__(self, k, v)

    def Delete(self):
        if "Delete" in self._fail:
            raise self._fail["Delete"]
        owner = self._owner
        if owner is not None and self in owner:
            owner.remove(self)

    def GetType(self):
        class _T:
            Name = getattr(self, "_type", type(self).__name__)

        return _T


class Namespace:
    """Auto-creating namespace for Ansys.Mechanical.DataModel.Enums.X.Y style references."""

    def __init__(self, path=""):
        self._path = path

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return Namespace(self._path + "." + k if self._path else k)

    def __repr__(self):
        return self._path

    def __str__(self):
        return self._path.rsplit(".", 1)[-1]


class Sel:
    Ids = None


class Output:
    """A load component's Output; a "Free" one (displacements) refuses SetDiscreteValue until
    DiscreteValues has been assigned, exactly as Mechanical 2026 R1 does."""

    def __init__(self, free=False):
        self.DiscreteValues = None
        self._free = free

    def SetDiscreteValue(self, i, q):
        if self._free and self.DiscreteValues is None:
            raise RuntimeError("Object reference not set to an instance of an object.")
        self.DiscreteValues = [q]


class Messages(list):
    """Mechanical's Messages pane; Clear() is what the old solve script called."""

    cleared = 0

    def Clear(self):
        self.cleared += 1
        self[:] = []


class NamedSel(NSObj, Obj):
    @property
    def Ids(self):
        return list(self.Location.Ids) if self.Location is not None else []


class LoadObj(Obj):
    """A load or support. Like the real thing it only takes a face/edge/vertex selection: a body
    selection leaves Location None (Remote Displacement keeps it but stays UnderDefined), and the
    object is UnderDefined until it has a usable scope."""

    needs_scope = True
    body_mode = "reject"

    def __setattr__(self, k, v):
        if k == "Location" and v is not None and self.needs_scope:
            kinds = kinds_of(v.Location if isinstance(v, NamedSel) else v)
            if "body" in kinds and self.body_mode == "reject":
                v = None
        Obj.__setattr__(self, k, v)

    @property
    def ObjectState(self):
        if not self.needs_scope:
            return "FullyDefined"
        loc = self.__dict__.get("Location")
        if loc is None:
            return "UnderDefined"
        kinds = kinds_of(loc.Location if isinstance(loc, NamedSel) else loc)
        if "body" in kinds:
            return "UnderDefined"
        return "FullyDefined"


def _ns_obj():
    return NamedSel("Selection", Location=None)


class Fake:
    """A model with one static analysis, a modal analysis, bodies, a mesh and some named selections."""

    def __init__(self, solved=False, geo_unit="m"):
        self.messages = Messages()
        self.created = []  # log of Add* calls
        self.model = Obj("Model")
        m = self.model
        m.Analyses = []
        m.NamedSelections = []
        m.ContactRegions = []
        m.Materials = Obj("Materials", Children=[Obj("Structural Steel"), Obj("Q235")])
        m.Geometry = Obj("Geometry")
        m.Geometry.Bodies = []
        m.Geometry.PointMasses = []
        m.Geometry.AddPointMass = lambda: self._add(
            m.Geometry.PointMasses, Obj("Point Mass", MassMomentOfInertiaX=None)
        )
        m.Geometry.IsPropertyVisible = lambda n: False
        m.Geometry.Model2DBehavior = "PlaneStress"
        m.Mesh = Obj("Mesh", ElementSize=Q(0.0, "m"), Nodes=100, Elements=60, Sizings=[])
        m.Mesh.Children = m.Mesh.Sizings
        m.Mesh.GenerateMesh = lambda: None
        m.Mesh.AddSizing = lambda: self._add(m.Mesh.Sizings, Obj("Sizing", ElementSize=Q(0.0, "m")))
        m.Mesh.GetChildren = lambda cat, flag: list(m.Mesh.Sizings)
        m.Connections = Obj("Connections")
        m.Connections.AddContactRegion = lambda: self._add(
            m.ContactRegions,
            Obj(
                "Contact Region",
                ContactType="Bonded",
                FrictionCoefficient=0.0,
                ObjectState="FullyDefined",
            ),
        )
        m.AddNamedSelection = lambda: self._add(m.NamedSelections, _ns_obj())
        m.GetChildren = self._model_children
        m.Geometry.GetChildren = self._geo_children
        self.static = self._analysis("Static Structural", "Static", solved)
        self.modal = self._analysis("Modal", "Modal", solved)
        m.Analyses = [self.static, self.modal]
        self.geo_unit = geo_unit
        self.assemblies = []
        self.ext = Obj("ExtAPI")
        self.ext.DataModel = Obj("DataModel")
        self.ext.DataModel.Project = Obj("Project", Model=m)
        self.ext.DataModel.GeoData = Obj("GeoData", Unit=geo_unit)
        self.ext.DataModel.GeoData.Assemblies = self.assemblies
        _KINDS.clear()

        def entity_by_id(i):
            kind = _KINDS.get(int(i))
            if kind is None:
                return None
            return type("Geo%sWrapper" % kind.capitalize(), (Obj,), {})("e", Id=int(i))

        self.ext.DataModel.GeoData.GeoEntityById = entity_by_id
        self.ext.SelectionManager = Obj("SelectionManager", CreateSelectionInfo=lambda kind: Sel())
        self.ext.Application = Obj("Application", Messages=self.messages)

    # -- construction helpers ---------------------------------------------------------------

    def _add(self, owner, obj, base=None):
        base = re.sub(r" \d+$", "", obj.Name)
        n = sum(1 for o in owner if re.sub(r" \d+$", "", o.Name) == base)
        if n:
            obj.Name = "%s %d" % (base, n + 1)
        obj._owner = owner
        owner.append(obj)
        self.created.append(obj.Name)
        return obj

    def _model_children(self, cat, flag):
        c = str(cat)
        if c == "NamedSelection":
            return list(self.model.NamedSelections)
        if c == "Body":
            return list(self.model.Geometry.Bodies)
        if c == "ContactRegion":
            return list(self.model.ContactRegions)
        return []

    def _geo_children(self, cat, flag):
        c = str(cat)
        if c == "Body":
            return list(self.model.Geometry.Bodies)
        if c == "PointMass":
            return list(self.model.Geometry.PointMasses)
        return []

    def _analysis(self, name, typ, solved):
        a = Obj(name, AnalysisType=typ)
        a.Children = []
        a.AnalysisSettings = Obj(
            "Settings",
            MaximumModesToFind=6,
            NumberOfSteps=1,
            LargeDeflection=False,
            AutomaticTimeStepping="ProgramControlled",
            DefineBy="Time",
            InitialSubsteps=1,
            MinimumSubsteps=1,
            MaximumSubsteps=10,
        )
        sol = Obj("Solution", Status="Done" if solved else "SolveRequired")
        sol.Children = []
        sol.solves = 0

        def solve(wait):
            sol.solves += 1
            if getattr(sol, "solve_raises", None):
                raise sol.solve_raises
            sol.Status = getattr(sol, "solve_result", "Done")

        sol.Solve = solve
        sol.EvaluateAllResults = lambda: setattr(sol, "evaluated", True)
        a.Solution = sol
        for meth, label in (
            ("AddEquivalentStress", "Equivalent Stress"),
            ("AddDirectionalDeformation", "Directional Deformation"),
            ("AddMaximumPrincipalStress", "Maximum Principal Stress"),
            ("AddMiddlePrincipalStress", "Middle Principal Stress"),
            ("AddMinimumPrincipalStress", "Minimum Principal Stress"),
            ("AddTotalDeformation", "Total Deformation"),
            ("AddNormalStress", "Normal Stress"),
            ("AddStressIntensity", "Stress Intensity"),
            ("AddStressTool", "Stress Tool"),
            ("AddForceReaction", "Force Reaction"),
        ):
            setattr(sol, meth, self._result_factory(sol, label, solved_ref=sol))
        for meth, label in (
            ("AddFixedSupport", "Fixed Support"),
            ("AddFrictionlessSupport", "Frictionless Support"),
            ("AddForce", "Force"),
            ("AddPressure", "Pressure"),
            ("AddDisplacement", "Displacement"),
            ("AddRemoteDisplacement", "Remote Displacement"),
            ("AddRemoteForce", "Remote Force"),
            ("AddMoment", "Moment"),
            ("AddEarthGravity", "Standard Earth Gravity"),
        ):
            setattr(a, meth, self._load_factory(a, label))
        return a

    def _load_factory(self, analysis, label):
        def make():
            o = LoadObj(
                label,
                Location=None,
                _type={"Standard Earth Gravity": "EarthGravity"}.get(label, label.replace(" ", "")),
            )
            o.needs_scope = label != "Standard Earth Gravity"
            o.body_mode = "underdefined" if label == "Remote Displacement" else "reject"
            free = label in ("Displacement", "Remote Displacement")
            for comp in (
                "XComponent",
                "YComponent",
                "ZComponent",
                "RotationX",
                "RotationY",
                "RotationZ",
                "Magnitude",
            ):
                setattr(o, comp, Obj(comp, Output=Output(free)))
            o.Direction = "NegativeZAxis"
            for hook in getattr(self, "load_hooks", []):
                hook(o)
            return self._add(analysis.Children, o)

        return make

    def _result_factory(self, sol, label, solved_ref):
        def make():
            o = Obj(label, _type=label.replace(" ", ""))
            o.ObjectState = "NotSolved"
            o.Maximum = Q(12.5, "Pa")
            o.Minimum = Q(-3.0, "Pa")
            o.Mode = 0
            o.ReportedFrequency = Q(0.0, "Hz")
            o.Location = None
            o.GeometryLocation = None
            o.XAxis, o.YAxis, o.ZAxis, o.Total = Q(1, "N"), Q(2, "N"), Q(3, "N"), Q(4, "N")
            for hook in getattr(self, "result_hooks", []):
                hook(o)
            return self._add(sol.Children, o)

        return make

    # -- content helpers --------------------------------------------------------------------

    def add_body(self, name, part="P", solid=True, faces=0, mass=1.0):
        b = Obj(name, Parent=Obj(part), Material="Structural Steel")
        b._owner = self.model.Geometry.Bodies
        self.model.Geometry.Bodies.append(b)
        gb = Obj(
            "geo",
            Id=len(self.model.Geometry.Bodies) + 100,
            BodyType="GeoBodySolid" if solid else "GeoBodySurface",
        )
        gb.Faces, gb.Edges, gb.Vertices = [], [], []
        b.GetGeoBody = lambda: gb
        # the volume is reported in mm^3, as a body in a mm unit system would: a script that reads
        # .Value without converting to m^3 is off by a factor of 1e9
        b.Mass, b.Volume = Q(mass, "kg"), Q(mass / 1000.0 * 1e9, "mm mm mm")
        b.CentroidX, b.CentroidY, b.CentroidZ = Q(0.0, "m"), Q(0.0, "m"), Q(0.0, "m")
        b.MomentOfInertiaIp1 = b.MomentOfInertiaIp2 = b.MomentOfInertiaIp3 = Q(0.01, "kg m m")
        b._geo = gb
        return b

    def add_face(self, body, fid, centroid, normal=(0.0, 0.0, 1.0), half=0.0):
        _KINDS[int(fid)] = "face"
        f = Obj("face", Id=fid, Centroid=list(centroid))
        c = list(centroid)
        f.GetBoundingBox = lambda _c=c, _h=half: [
            _c[0] - _h,
            _c[1] - _h,
            _c[2] - _h,
            _c[0] + _h,
            _c[1] + _h,
            _c[2] + _h,
        ]
        f.NormalAtParam = lambda u, v, _n=normal: _n
        body._geo.Faces.append(f)
        return f

    def build_geo(self):
        """Mirror the bodies into GeoData.Assemblies > Parts > Bodies for the box/id lookups."""
        part = Obj("part", Bodies=[b._geo for b in self.model.Geometry.Bodies])
        for b in self.model.Geometry.Bodies:
            _KINDS[int(b._geo.Id)] = "body"
            for e in b._geo.Edges:
                _KINDS[int(e.Id)] = "edge"
            for v in b._geo.Vertices:
                _KINDS[int(v.Id)] = "vertex"
        for b in self.model.Geometry.Bodies:
            b._geo.Name = b.Name
        self.assemblies[:] = [Obj("asm", Parts=[part])]

    def add_ns(self, name, ids=None):
        ns = self.model.AddNamedSelection()
        ns.Name = name
        if ids is not None:
            sel = Sel()
            sel.Ids = list(ids)
            ns.Location = sel
        return ns

    # -- running scripts --------------------------------------------------------------------

    def env(self):
        e = {
            "ExtAPI": self.ext,
            "Quantity": Q,
            "DataModelObjectCategory": Namespace("DataModelObjectCategory"),
            "SelectionTypeEnum": Namespace("SelectionTypeEnum"),
            "LoadDefineBy": Namespace("LoadDefineBy"),
            "ContactType": Namespace("ContactType"),
            "Ansys": Namespace("Ansys"),
        }
        return e

    def run(self, script, shared=None, keep_markers=False):
        """Execute a generated script in a fresh namespace; return everything it printed."""
        buf = io.StringIO()
        scope = dict(self.env()) if shared is None else shared
        with redirect_stdout(buf):
            try:
                exec(compile(script, "<script>", "exec"), scope)
            except Exception as e:  # mirrors the wrapper's "Script error:" line
                print("Script error: " + str(e))
        out = buf.getvalue().strip()
        if not keep_markers:
            out = chr(10).join(ln for ln in out.splitlines() if not ln.startswith("@@ANALYSIS "))
        return out


def last_json(raw: str):
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return None
