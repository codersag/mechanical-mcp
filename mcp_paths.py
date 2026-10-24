"""Path policy and privacy helpers shared by the Mechanical and ACP servers.

Tool arguments come from an AI agent that may have read untrusted text, so every file a server
reads or writes is confined to an allow-list of folders, and nothing that identifies the person
running the server (profile paths, user name, computer name, licence ids, e-mail addresses) is
handed back to the model.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import re
from pathlib import Path, PureWindowsPath

#: Control characters (other than tab, newline, carriage return) and the Unicode line separators
#: have no business in a tool argument.
BAD_CHARS = frozenset(
    [chr(c) for c in (*range(0, 9), 11, 12, *range(14, 32), *range(127, 160))]
    + [chr(0x2028), chr(0x2029)]
)

MAX_READ_BYTES = 256 * 1024 * 1024


def is_network_path(p) -> bool:
    """UNC and device paths in every spelling, and drive-less rooted paths. Decided on the text
    alone, before the file system is touched, because resolving a UNC path opens a network
    connection and can leak credentials."""
    return str(p).replace("/", chr(92)).startswith(chr(92))


def path_under(p: Path, roots) -> bool:
    for r in roots:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def too_broad(p: Path) -> bool:
    """A folder that is not a sensible place to confine file access to."""
    try:
        p = p.resolve()
        if p == Path(p.anchor):
            return True
        home = Path.home().resolve()
        if p == home or p in home.parents:
            return True
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            v = os.environ.get(var)
            if v:
                vp = Path(v).resolve()
                if p == vp or vp in p.parents:
                    return True
    except (OSError, ValueError):
        return True
    return False


def roots(*env_names: str) -> list:
    """The allowed folders: the first of env_names that is set (an explicit list is
    authoritative, even when every entry is unusable), else the working directory unless that is
    too broad."""
    for env in env_names:
        configured = os.environ.get(env, "")
        if configured.strip():
            out: list = []
            for part in configured.split(os.pathsep):
                part = part.strip()
                if part and not is_network_path(part):
                    try:
                        out.append(Path(part).expanduser().resolve())
                    except (OSError, ValueError):
                        continue
            return out
    cwd = Path.cwd().resolve()
    return [] if too_broad(cwd) else [cwd]


_DEVICE = re.compile(r"^(con|prn|aux|nul|conin\$|conout\$|(com|lpt)[1-9¹²³])$", re.IGNORECASE)


def is_device_name(part: str) -> bool:
    """Windows reserves these names with any extension (aux.json, COM1.txt) and ignores trailing
    dots and spaces; they open devices, not files."""
    stem = part.split(".")[0].rstrip(" .")
    return bool(_DEVICE.match(stem))


def allow(path, *, env_names=("MECHANICAL_MCP_ROOTS",), exts=None, write=False, overwrite=False):
    """(resolved Path, None) if path may be used, else (None, message).

    exts: allowed lower-case suffixes. write: the file will be created (its folder must exist and
    the file must not, unless overwrite). Without write the file must exist and be a regular file
    no larger than MAX_READ_BYTES. Messages never print an absolute profile path or the list of
    allowed folders (only their number).
    """
    try:
        raw = str(path)
        if not raw.strip():
            return None, "A path is required"
        if len(raw) > 4096 or any(c in BAD_CHARS for c in raw):
            return None, "Invalid path"
        if is_network_path(raw):
            return None, "Network paths are not allowed"
        pure = PureWindowsPath(raw)
        parts = pure.parts[1:] if pure.anchor else pure.parts
        if any(":" in part for part in parts):
            return None, "':' is only allowed in a drive letter (no alternate data streams)"
        bad = next((part for part in parts if is_device_name(part)), None)
        if bad is not None:
            return None, "%r is a reserved Windows device name and cannot be a file name" % bad
        p = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as e:
        return None, "Invalid path: %s" % redact(str(e))
    allowed = roots(*env_names)
    if is_network_path(p) or not path_under(p, allowed):
        return None, (
            "%s is outside the folders this server may use (%d allowed; set %s)"
            % (redact(str(p)), len(allowed), env_names[0])
        )
    if exts is not None and p.suffix.lower() not in exts:
        return None, "The file extension must be one of: %s" % ", ".join(sorted(exts))
    try:
        if write:
            if not p.parent.is_dir():
                return None, "The folder does not exist: %s" % redact(str(p.parent))
            if Path(raw).expanduser().is_symlink():
                return None, "Refusing to write through a symbolic link"
            if p.exists():
                if not p.is_file():
                    return None, "%s exists and is not a regular file" % redact(str(p))
                if not overwrite:
                    return None, "%s already exists; pass overwrite=true to replace it" % redact(
                        str(p)
                    )
                if p.stat().st_nlink > 1:
                    return None, "Refusing to overwrite a hard-linked file"
        else:
            if not p.is_file():
                return None, "File not found: %s" % redact(str(p))
            if p.stat().st_size > MAX_READ_BYTES:
                return None, "File is larger than %d MB" % (MAX_READ_BYTES // (1024 * 1024))
    except OSError as e:
        return None, "Cannot access %s: %s" % (redact(str(p)), redact(str(e)))
    return p, None


# ---------------------------------------------------------------------------
# Redaction: nothing that identifies the person running the server goes to the model
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_LICENCE_SERVER = re.compile(r"\b\d{3,5}@[A-Za-z0-9_.-]+")
_LICENCE_FIELD = re.compile(
    r"(?i)\b(customer(?:[ _]*(?:id|number|no\.?))?|licen[sc]e[ _]*server|checkout[ _]*(?:id|key)?|"
    r"session[ _]*id|host[ _]*id|entitlement[ _]*id)\s*[:=]\s*[^\s,;\"']+"
)
_ACL = re.compile(r"(?i)(-acl\s+)[0-9a-f]{12,}")
_HOSTNAME_LINE = re.compile(r"(?i)(hostname\s*:\s*)\S+")


def redaction_enabled() -> bool:
    return os.environ.get("MECHANICAL_MCP_REDACT", "1").strip() != "0"


def _names() -> tuple[list[str], list[str]]:
    users, hosts = [], []
    for v in (os.environ.get("USERNAME"), os.environ.get("USER"), os.environ.get("LOGNAME")):
        if v:
            users.append(v)
    try:
        users.append(getpass.getuser())
    except Exception:  # noqa: BLE001
        pass
    for v in (os.environ.get("COMPUTERNAME"), os.environ.get("HOSTNAME"), platform.node()):
        if v:
            hosts.append(v)
    return (
        sorted({u for u in users if len(u) >= 3}, key=len, reverse=True),
        sorted({h for h in hosts if len(h) >= 3}, key=len, reverse=True),
    )


def _profile_variants() -> list[str]:
    out = set()
    for base in (os.environ.get("USERPROFILE"), str(Path.home())):
        if not base or len(base) < 4:
            continue
        base = base.rstrip("\\/")
        out.add(base)
        out.add(base.replace("\\", "/"))
        out.add(base.replace("\\", "\\\\"))  # as it appears inside JSON text
        out.add(base.replace("/", "\\").replace("\\", "\\\\"))
    return sorted(out, key=len, reverse=True)


def redact(text) -> str:
    """Replace what identifies the person running the server: the user-profile prefix of paths
    (-> %USERPROFILE%), the Windows user name, the computer name, licence server / customer /
    checkout / session ids and e-mail addresses. Set MECHANICAL_MCP_REDACT=0 to switch off."""
    s = text if isinstance(text, str) else str(text)
    if not redaction_enabled():
        return s
    for v in _profile_variants():
        s = re.sub(re.escape(v), lambda m: "%USERPROFILE%", s, flags=re.IGNORECASE)
    users, hosts = _names()
    for name in users:
        s = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
            "<user>",
            s,
            flags=re.IGNORECASE,
        )
    for name in hosts:
        s = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
            "<host>",
            s,
            flags=re.IGNORECASE,
        )
    s = _HOSTNAME_LINE.sub(lambda m: m.group(1) + "<host>", s)
    s = _ACL.sub(lambda m: m.group(1) + "<redacted>", s)
    s = _LICENCE_SERVER.sub("<licence-server>", s)
    s = _LICENCE_FIELD.sub(lambda m: m.group(1) + ": <redacted>", s)
    s = _EMAIL.sub("<email>", s)
    return s


def project_identity(path) -> dict:
    """What is remembered about a project: a hash of its folder path and the folder NAME only,
    never the path (which contains the user name)."""
    raw = str(path or "").strip()
    if not raw:
        return {"id": "", "name": ""}
    norm = os.path.normcase(raw.replace("/", "\\")).rstrip("\\")
    name = PureWindowsPath(raw.rstrip("\\/")).name
    return {"id": hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16], "name": redact(name)}
