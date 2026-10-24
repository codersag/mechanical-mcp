# ANSYS Mechanical & ACP MCP Servers

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that connects any MCP-compatible AI client to ANSYS Mechanical via [PyMechanical](https://mechanical.docs.pyansys.com/) gRPC. Talk to your simulation in plain English.

The repo also ships a second server, **`ansys-acp-mcp`**, for building composite layups in ANSYS Composite PrepPost (ACP) through PyACP. See [ACP server](#acp-server-ansys-acp-mcp).

https://github.com/user-attachments/assets/309952dc-dce0-4aa6-9b06-e8047f32c2de

## Compatible clients

Works with any MCP-compatible client, including:

- [Claude Desktop](https://claude.ai/download)
- [Cursor](https://www.cursor.com)
- [Windsurf](https://windsurf.ai)
- [VS Code](https://code.visualstudio.com) (with Copilot MCP support)
- [Continue](https://continue.dev)

## What it does

| Category | Tools |
|---|---|
| **Connection** | `connect_to_mechanical`, `check_mechanical_connection`, `disconnect_from_mechanical` (one session on this machine at a time) |
| **Model** | `get_model_info`, `list_materials`, `assign_material`, `suppress_bodies`, `set_2d_behavior` |
| **Mesh** | `set_mesh_element_size`, `set_body_mesh_size`, `generate_mesh`, `get_mesh_statistics` |
| **Connections** | `add_contact`, `set_contact_type`, `list_contact_regions` |
| **Loads and supports** | `add_fixed_support`, `add_frictionless_support`, `add_displacement`, `add_remote_displacement`, `add_force`, `add_remote_force`, `add_pressure`, `add_moment`, `add_standard_gravity`, `list_boundary_conditions` |
| **Analysis settings** | `set_nonlinear_settings` |
| **Solve** | `solve_analysis` (blocking or background), `get_solve_status` |
| **Results** | `add_total_deformation`, `add_total_deformation_all_modes`, `add_directional_deformation`, `add_equivalent_stress`, `add_normal_stress`, `add_principal_stress`, `add_stress_intensity`, `add_stress_tool`, `add_reaction_force`, `list_results`, `get_result_values`, `get_modal_frequencies` |
| **Post-processing** | `export_nodal_results`, `linearize_path` (needs the `postproc` extra) |
| **Point mass** | `convert_part_to_point_mass`, `convert_prefix_to_point_mass`, `list_point_masses` |
| **Named selections** | `create_named_selection`, `list_named_selections`, `delete_named_selection` |
| **Reports** | `generate_report` (TXT, or DOCX with the `report` extra) |
| **Scripting** | `run_mechanical_script` - **off by default**, see [Security](#security-and-configuration) |

Every tool that changes the model names the analysis it used (`analysis`: index, name, type). With
several analyses in the model, `analysis_index` or `analysis_name` must be given (index 0 is often
not the one meant); `expect_analysis_type` refuses a mismatch. Set
`MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS=0` to restore the old default of index 0.

## What `ok: true` means

Every tool answers with a JSON object that has an `ok` key. For tools that create something in the
model, `ok: true` means *the object was created, scoped and read back* - it does **not** mean the
result is valid. A result added before the analysis is solved comes back with `max: null`,
`min: null`, `solved: false` and a note (Mechanical itself would report `0`). If anything fails after
an object was created, the object is deleted again and the response says `rolled_back: true`.

## Requirements

- ANSYS Mechanical **2026 R1** on Windows. Only 2026 R1 (v261) has been exercised; older releases may
  lack API members used here (the gravity and reaction-force calls differ between releases) and are untested.
- Python 3.10+, `ansys-mechanical-core`, `mcp[cli]>=1,<2`

## Installation

```bash
git clone https://github.com/codersag/mechanical-mcp.git
cd mechanical-mcp
pip install -e .
```

Optional extras:

```bash
pip install -e ".[report]"    # python-docx: generate_report(fmt="docx")
pip install -e ".[postproc]"  # numpy + scipy: linearize_path
```

## MCP Client Configuration

Add the following to your MCP client's config file. The config file location varies by client:

| Client | Config file location |
|---|---|
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (Mac) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor | `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` globally |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |

```json
{
  "mcpServers": {
    "mechanical-mcp": {
      "command": "ansys-mechanical-mcp"
    }
  }
}
```

Or if running from source:

```json
{
  "mcpServers": {
    "mechanical-mcp": {
      "command": "python",
      "args": ["C:/path/to/mechanical-mcp/mechanical_mcp_server.py"]
    }
  }
}
```

Restart your client after editing the config.

## Finding the Mechanical gRPC port

Mechanical exposes a gRPC server whose port you pass to `connect_to_mechanical`.

In Workbench (`Tools → Scripting → Run Script`):

```python
port = GetSystem("SYS").GetContainer(ComponentName="Model").StartGrpcServer()
print(port)
```

If you have multiple Mechanical systems open (e.g. Modal + Static Structural), run the script for each system by replacing `"SYS"` with the system name (e.g. `"SYS 1"`, `"SYS 2"`).

## Usage

1. Open ANSYS Mechanical 2026 R1 and start its gRPC server (see above)
2. Find the port using one of the methods above
3. Connect to your Mechanical instance:

> "Connect to Mechanical on port 10000"

Then interact naturally:

> "Assign Structural Steel to the body called Plate"

> "Generate a mesh with 50mm element size"

> "Add a fixed support to the named selection called Base"

> "Solve the analysis and get the maximum von Mises stress"

> "Convert part Equipment1 to a point mass"

## One session at a time

The server holds **one** connection. Calling `connect_to_mechanical` with another port replaces it,
but only once the new session answers (a dead or wrong port leaves the working connection alone).
Only sessions on this machine are supported: the port is probed on `127.0.0.1` first so a dead port
fails fast, and a port that accepts connections but never speaks gRPC is detected by a short
protocol probe instead of hanging the handshake. The last port is remembered in a per-user folder
(`%LOCALAPPDATA%\mechanical-mcp`, override with `MECHANICAL_MCP_STATE_DIR`) so a restarted server can
reconnect; only the port, a hash of the project folder and the folder's NAME are stored, never a path. A call that fails in transit (timeout, dropped channel) is **never re-sent**: Mechanical may
or may not have run it, so the response says so and you should look at the model before retrying.

## Security and configuration

Tool arguments come from an AI agent that may have read untrusted text, so the server is restrictive
by default.

| Variable | Meaning |
|---|---|
| `MECHANICAL_MCP_ROOTS` | Folders (separated by `;` on Windows) that `export_nodal_results`, `linearize_path`, `generate_report` and the ACP server's file tools may read/write. Default: the server's working directory, unless that is a drive root, your home folder or a parent of it, or a Windows/Program Files folder - then nothing is allowed until you set this. |
| `ACP_MCP_ROOTS` | Same for the ACP server; falls back to `MECHANICAL_MCP_ROOTS`, then to the working directory. |
| `MECHANICAL_MCP_REDACT` | `1` (default) removes your profile path, user name, computer name, licence server / customer / session ids and e-mail addresses from every reply; `0` switches that off. |
| `MECHANICAL_MCP_REQUIRE_EXPLICIT_ANALYSIS` | `1` (default): a tool that changes the model must be told which analysis when there are several. `0` uses index 0. |
| `MECHANICAL_MCP_ENABLE_SCRIPT` | Set to `1` to enable `run_mechanical_script`, which runs arbitrary IronPython in your Mechanical session and bypasses every limit below. Off by default. |
| `MECHANICAL_MCP_STATE_DIR` | Where the remembered port is stored. |
| `MECHANICAL_MCP_LOCK_WAIT_S` | How long a call waits for Mechanical while another call (a solve) is running, default 60. |
| `MECHANICAL_MCP_MAX_WAITERS` | How many calls may queue for the Mechanical lock before the next gets `busy` at once, default 4. |
| `MECHANICAL_MCP_MAX_EXPORT_NODES` / `MECHANICAL_MCP_EXPORT_TIMEOUT_S` | Size and time budget of `export_nodal_results` (it asks Mechanical for one node at a time inside the lock), defaults 500,000 and 120. |
| `MECHANICAL_MCP_PROBE_WAIT_S` | How long status tools (`check_mechanical_connection`, `get_solve_status`, `disconnect_from_mechanical`) wait before answering "busy", default 1. |
| `MECHANICAL_MCP_CONNECT_TIMEOUT_S` | Bound on the gRPC handshake and first script when connecting, default 20. |
| `MECHANICAL_MCP_SHUTDOWN_WAIT_S` | How long `disconnect(shutdown=True)` waits for the session's port to close, default 15. |
| `MECHANICAL_MCP_MAX_NODES` / `MECHANICAL_MCP_LINEARIZE_TIMEOUT_S` | Size and time limits for `linearize_path`, defaults 1,000,000 and 60. |

Files: network/UNC paths and `:` (alternate data streams) are refused from the text, only `.json`
(export, linearize) or `.docx`/`.txt` (report) are accepted, and an existing file is never replaced
unless you pass `overwrite=true`. `export_nodal_results` enforces this inside the Mechanical script
as well: it writes a uniquely named exclusive temporary file, checks again right before the final
rename and refuses (leaving the file) if one appeared meanwhile, and removes only the temporary file
it created. Reserved Windows device names (`aux.json`, `COM1.txt`, `nul`, ...) are refused. `disconnect_from_mechanical(shutdown=True)` force-exits the
Mechanical session whoever started it (unsaved work is lost), so it also needs
`confirm="SHUT DOWN MECHANICAL"`; the normal `disconnect_from_mechanical()` only drops the connection.

Example with an allow-list and scripting enabled:

```json
{
  "mcpServers": {
    "mechanical-mcp": {
      "command": "ansys-mechanical-mcp",
      "env": { "MECHANICAL_MCP_ROOTS": "C:\\Work\\fea", "MECHANICAL_MCP_ENABLE_SCRIPT": "0" }
    }
  }
}
```

## Privacy

The server is meant to be usable on a work machine without telling the AI model, its logs or this
repository who you are.

- **Replies are redacted** (on by default, `MECHANICAL_MCP_REDACT=0` switches it off): the
  user-profile prefix of a path becomes `%USERPROFILE%`, and your Windows user name, computer name,
  licence server (`port@host`), customer / checkout / session / host ids and e-mail addresses are
  replaced in every tool reply and error. This is pattern-based: a project that has your name in its
  own file or folder names, or text Mechanical returns in a form the patterns do not know, can still
  carry it. Use a neutral project folder name if that matters.
- **Stored state is minimal.** The remembered-session file holds the port, a 16-character hash of
  the project folder and the folder's name; no path, no user name.
- **File access is confined** to the folders in `MECHANICAL_MCP_ROOTS` / `ACP_MCP_ROOTS`; error
  messages report how many folders are allowed, never which.
- **Nothing leaves the machine.** The server talks to Mechanical on `127.0.0.1` and makes no network
  requests; it does not log tool arguments to a file. Temporary script and output files are removed
  after each call and stale ones (older than a day) at start-up.
- The repository's `.gitignore` excludes project files (`*.mechdb`, `*.wbpj`), caches, logs and
  local configuration; `tests/test_privacy.py` checks the published files for profile paths,
  e-mail addresses and licence ids.

## Geometry selection, nonlinear setup and post-processing

Load and support tools (`add_fixed_support`, `add_frictionless_support`, `add_displacement`, `add_force`, `add_pressure`) take a named selection, or `""` plus either:

- `entity_ids`: comma-separated geometry ids, or
- `box_mm`: `"xmin,xmax,ymin,ymax,zmin,zmax"` in mm (blank = open side), with `entity_type` (`face`, `edge`, `vertex`) and optional `body_name`.

Box coordinates are compared in mm whatever unit the geometry uses (read from `GeoData.Unit`).
A missing or empty named selection, ids that do not exist, or a box that matches nothing is an error
and nothing is created. `entity_ids` may name faces, edges or vertices.

Inspecting the model: `list_boundary_conditions` (loads and supports with scope and key values),
`list_contact_regions`, `list_results` and `get_result_values` (Max/Min with unit once solved),
`list_named_selections`, `create_named_selection` (from body names or prefix, geometry ids or a box;
refuses empty results and duplicate names unless `replace=true`).

Other model and result tools: `set_2d_behavior`, `set_body_mesh_size`, `set_nonlinear_settings`
(changes only the settings you pass), `set_contact_type` (**overwrites every contact region** unless
limited with `region_names` / `only_if_currently`; the response lists each region's previous type),
`add_contact` (two or more bodies), `add_normal_stress`, `add_stress_intensity`,
`export_nodal_results` and `linearize_path`.

### Stress linearization flow

1. `add_normal_stress` (a signed component; linearizing von Mises or stress intensity is refused).
2. `solve_analysis` (use `wait=false` + `get_solve_status` for long solves).
3. `export_nodal_results("Normal Stress", "C:/Work/fea/n.json")` - writes node ids, `xyz_mm`
   (millimetres), `values`, `value_unit`, and whether the quantity is signed. Refuses an unsolved model.
4. `linearize_path("C:/Work/fea/n.json", "0,0,0", "0,0,12")` - a 3D section line, or two numbers in a
   `plane` (then the data must lie in one plane, or give `slice_mm`). Returns membrane, bending (at
   the start point; the end point has the opposite sign) and peak = total - (membrane + bending),
   with `section_length_mm`.

**Not verified against a real solved model:** steps 3-4 depend on `Result.PlotData`, which only exists
after a solve, and the server's tests never solve. The export script, units and file format are
covered by unit tests with a fake object model; the numerical linearization is tested against
analytic profiles (pure membrane, pure bending, linear + peak). Treat the first real run with care.

## Known limits

- **Sign convention of `add_pressure` is not verified here.** The magnitude is passed to Mechanical
  as given; which way a positive value acts was not checked (it needs a solve and a look at the
  result), so confirm it in Mechanical before relying on the direction.
- `set_contact_type` snapshots the type AND the friction coefficient of every region it changes and
  restores both if any region fails; the response lists the real before/after of both.
- A client-side timeout on `solve_analysis` marks the solve as possibly still running: the next
  solve request is refused until you check and pass `confirm_not_running=true`.
- A finished background solve's result is kept until read (up to five, one hour) and attached to the
  poll for the same analysis.

- **Verified live (Mechanical 2026 R1, an unsolved copy of a project, nothing solved or saved):**
  gravity, loads and supports (named selection / ids / box scopes), remote displacement, reaction-force
  probe creation, results created before a solve, modal/static checks, mesh size, body sizing,
  nonlinear settings, contact type changes, material and suppression changes, point-mass conversion
  (it reproduces the previous implementation's centre of gravity and attachment faces, which shows
  agreement, not that the inertia is right - see the next point), reports (TXT), non-ASCII names
  including an emoji, list_point_masses in StandardMKS / CGS / NMM / BIN / UMKS, named-selection
  creation and dependents, the reuse of identical results and contacts, a connection to a silent
  socket (refused after about 2 s by the protocol probe, the existing connection untouched), a
  load scoped to a body-type named selection (refused and rolled back; Mechanical leaves such a load
  without a scope), the analysis-selection checks (explicit analysis, name, expected type) on a model
  with three analyses, deleting a named selection by object id, the reparse-point link test
  (`System.IO.File.GetAttributes`) on a junction, a regular file and a folder, redaction of paths in
  replies, and recovery after a transport failure (exit code 0 in a subprocess).
- **`generate_mesh` was only run on a model that already had a mesh** (it returned the existing
  counts at once); meshing a fresh model and the failure path were not exercised live.
- **Never run live (needs a solve, which these tests forbid):** values read from solved results
  (`max`/`min`, reaction force x/y/z/total, modal frequencies), `export_nodal_results` on real data,
  the background solve with a real solver, `generate_report(fmt="docx")` with real python-docx
  (tested with a fake `docx` module), `set_2d_behavior` on a truly 2D model, and `convert_*_to_point_mass`
  on a model whose loads are scoped to the converted bodies (covered by unit tests with the fake
  model only).
- Standard Earth Gravity only supports +-X, +-Y, +-Z (always 1 g). Any other direction is an error.
- Mechanical 2026 R1 exposes no solve cancel, so a running solve cannot be stopped from here.
  `solve_analysis(wait=false)` runs the blocking solve on a background thread; other Mechanical tools
  report "busy" until it ends, and `get_solve_status` answers `Solving` without touching Mechanical.
- Materials are created in Workbench Engineering Data, not by this server; `assign_material` only
  accepts names that already exist there.
- Numeric limits (the code enforces exactly these): element sizes 0.001 to 100000 mm, proximity
  multiplier 0.1 to 100, friction 0 to 10, forces and moments at most 1e12 in magnitude, pressure
  1e15 Pa, displacements 1e9 mm, rotations 36000 deg, `samples` 2 to 5000.
- **Point-mass inertia is only exact when the principal axes are the global axes.** Mechanical gives
  a body only its three PRINCIPAL moments of inertia (`MomentOfInertiaIp1-3`; no tensor, no axes, no
  products of inertia). They are used as Ixx, Iyy, Izz and the response says `inertia_basis:
  "principal"` with warnings: always one, one more when several bodies are combined (their products
  of inertia are dropped because a point mass cannot take them), and one when planar faces are not
  aligned with the global axes. A body rotated against the global axes gets wrong Ixx/Iyy; check or
  set the inertia by hand in that case. Mass, centre of gravity and the attachment faces are
  unaffected.
- **A box selects by centroid.** `box_mm` picks the faces/edges/vertices whose CENTROID lies inside the
  box, not the entities that lie entirely inside; the response counts the selected entities that
  stick out in `warnings`, and `fully_inside=true` requires the whole entity (face and edge
  bounding boxes) to be inside.
- **Names are not unique in Mechanical** (two contact regions can both be "Contact Region").
  `set_contact_type(region_names=...)` matches every region of that name and says so;
  `list_contact_regions` shows `index` and `duplicate_names`. `delete_named_selection` deletes every
  selection of that name.
- **Idempotency.** The result tools and `add_contact` return an identical existing object
  (`already_exists: true`, `created: false`, and `suppressed` - a reused object may be suppressed)
  instead of adding "Equivalent Stress 2"; pass `reuse_existing=false` to force a duplicate. A
  contact region counts as the same when it joins exactly the same entities with the same type and
  (frictional) friction coefficient. If a region joins them but differs, `add_contact` refuses and
  lists the differences; `update_existing=true` changes the type/friction (restored on failure) and
  reports `updated`. Behaviour and formulation are not set by the call; they are only reported in
  `existing_properties`. Point-mass conversion refuses to run twice for the
  same EXACT prefix/part (the mass would count twice) unless `allow_duplicate=true`: the selection
  it makes is named `NearestFace_<start of prefix>_<hash of the whole prefix>`, so `SYS\\B` and
  `SYS\\Backing` never collide and are both allowed. Two different prefixes that match the same
  body are not recognised as duplicates.
  `delete_named_selection` refuses while loads, results, contacts, point masses or mesh controls are
  scoped to the selection (it lists them) unless `force=true`. Only objects scoped through a
  `Location`, `GeometryLocation`, `SourceLocation` or `TargetLocation` are recognised: Force-reaction
  probes (their scope is a copy of the entities), joints, springs, remote points, coordinate
  systems and anything else that references a selection some other way are NOT detected.
- **Transport failures.** If a call fails in transit (timeout, dropped channel) the script is not
  re-sent and the response says it may or may not have run. The connection object is dropped
  *outside* the exception handler and finalized before its channel is closed: doing it inside the
  handler made the whole server process crash (exit 139) because the failing frames kept the
  object alive. The handshake with a port that is not Mechanical is bounded
  (`MECHANICAL_MCP_CONNECT_TIMEOUT_S`, default 20 s) and does not hold the Mechanical lock; a late
  connection that arrives after the timeout is dropped the same way.
- **Honest status while Mechanical is busy.** During a blocking solve `check_mechanical_connection`
  answers `connected: true, busy: true`, `get_solve_status` answers `status: "Busy"` and
  `disconnect_from_mechanical` answers busy, each within
  `MECHANICAL_MCP_PROBE_WAIT_S` (1 s) plus scheduling. The guarantee is precise: these three tools
  run on their own small thread pool and never queue for the lock, so other waiting calls cannot
  starve them. Every other Mechanical tool waits for the lock up to `MECHANICAL_MCP_LOCK_WAIT_S`
  (60 s), but at most `MECHANICAL_MCP_MAX_WAITERS` (4) calls wait at a time; the next ones get
  `busy: true` immediately. A connection attempt to a port that never answers takes at most
  `MECHANICAL_MCP_CONNECT_TIMEOUT_S` and at most two such attempts may be pending.
- **Reconnecting.** The remembered port is stored with a hash of the project folder (and its name).
  The identity is read when you connect: if the project in the session is later saved under another
  name or a different project is opened in the same session, the remembered identity is stale
  until you connect again. A silent re-attach is only
  made while the port still serves that project (otherwise the call fails and tells you to connect
  deliberately), is reported once as `reconnected_to` in the tool output, and the port is forgotten
  when it refuses connections or after three bad answers in a row (tested), not after one; a busy
  or slow session (handshake timeout) is neither a refusal nor counted. A remembered session with
  no project identity (an unsaved project) is never re-attached silently: call
  `connect_to_mechanical`.
- **linearize_path** merges coincident nodes (interface nodes of touching bodies; their values are
  averaged) and triangulates only the nodes near the line (a k-d tree), so 216k nodes take seconds.
  A line that crosses a hole or notch is a `gap` error carrying `gap_diagnostics` (largest span and
  where it is); `allow_gaps=true` accepts it and reports `interpolated_across_gap`. The criterion
  is local and relative: the simplex a sample falls in is a gap only when its longest edge is more
  than 3 times the median of the simplices around it AND fewer than 20 % of those neighbours are of
  similar size. Graded, polar and long-element meshes (10:1, line along the long side or across it)
  and an abrupt step in element size (a fine region next to a coarse one) are therefore not
  flagged, but a void smaller than about three element sizes is not detected either. A section line
  shorter than 0.1 % of the model or than one element is refused. A line end that lies outside the triangulation by less than
  `snap_fraction` (default 0.25) of an element - on a curved boundary or a hair outside a flat
  face - is extrapolated from the nearest simplex and listed in `endpoint_adjustments`; farther out
  the line is refused. `MECHANICAL_MCP_MAX_NODES` (default 1,000,000) and
  `MECHANICAL_MCP_LINEARIZE_TIMEOUT_S` (default 60) bound the work; a timed-out run is told to
  stop (it checks a cancel flag between stages) and only one linearization may run at a time; a
  second call gets `busy` until the stopped one has ended. Only the single exported scalar is linearized: the file
  records its `orientation`; `expect_component` makes a mismatch an error (and is reported as
  `component_unverified` when the file has none); a line running along another axis than the
  component is refused unless `accept_direction_mismatch=true`; components are never combined.
- **Export step and time.** `export_nodal_results` exports the result at the step it is currently
  set to; an analysis with several load steps needs an explicit `load_step`, and the file records
  the step, substep and time (`step`). Time-history results are exported one step per file.
- **Exported units.** `PlotData` does not declare its unit. The export compares its largest value with
  the result's own `Maximum` and records `value_unit_verified`; when it is false the unit label is
  unconfirmed. This has only been unit-tested (a solve is needed to run it for real).
- **Explicit Dynamics** analyses keep displacement as a time table; the value you set is the last
  entry (the first is 0 at t=0). `set_nonlinear_settings` on an analysis without those settings
  returns a clear error.
- `get_solve_status` messages come from Mechanical's global Messages pane (all analyses); the response
  says so.
- One Mechanical session per server process; sessions on other machines are not supported.
- Generated scripts run in a private copy of Mechanical's globals, so nothing leaks between tools;
  `run_mechanical_script` (when enabled) shares Mechanical's persistent globals as before.

## ACP server (`ansys-acp-mcp`)

A second MCP server, `acp_mcp_server.py`, lets an AI assistant build **ANSYS Composite PrepPost (ACP)** layups and solid models through **PyACP** (`ansys-acp-core`), then convert the exported solid mesh into a clean per-fibre-angle external model for a Mechanical coupled-field analysis (for example thermal-electric lightning studies). Every tool returns JSON with `ok` and a short, specific error on failure.

Tested with ACP 2026 R1, `ansys-acp-core` 0.3.2, `mcp` 1.30, Windows, Python 3.10.

### Install

```bash
pip install -e ".[acp]"
```

ACP must be installed (the server uses `acp_grpcserver.exe` from `%AWP_ROOT261%\ACP`). Set `AWP_ROOT261` (or the equivalent for your release) if ANSYS is not in the default location.

```json
{
  "mcpServers": {
    "ansys-acp": {
      "command": "ansys-acp-mcp",
      "env": { "AWP_ROOT261": "C:\\Program Files\\ANSYS Inc\\v261" }
    }
  }
}
```

### Tools

| Group | Tool | What it does |
|---|---|---|
| Session | `acp_launch`, `acp_status`, `acp_shutdown` | start / inspect / stop the PyACP-managed ACP server |
| Model I/O | `acp_import_model`, `acp_save_model`, `acp_model_tree` | import CDB/H5/DAT/`acp:h5`, save `.acph5`, list objects and statuses |
| Definition | `acp_create_material`, `acp_create_fabric`, `acp_create_rosette`, `acp_create_oriented_selection_set` | materials, fabrics, rosettes, oriented selection sets |
| Layup | `acp_create_layup` | modeling group with one ply per angle (+ optional bulk ply) |
| Solid | `acp_create_solid_model`, `acp_update`, `acp_export_solid_model` | extrude, recompute, export CDB/H5 |
| CDB (no ACP needed) | `acp_inspect_cdb`, `acp_prepare_external_model` | summarise a CDB; convert an ACP solid CDB into a per-angle external model (normalised through MAPDL) |
| Workbench ACP | `acp_wb_build_script`, `acp_wb_commands`, `acp_wb_ply_split_snippet` | generate the script that builds the layup inside Workbench's own ACP, the Workbench commands that run and link it, and the Mechanical snippet that splits plies |

Typical flow: `acp_launch` → `acp_import_model` (shell mesh) → `acp_create_material` → `acp_create_fabric` → `acp_create_rosette` → `acp_create_oriented_selection_set` → `acp_create_layup` → `acp_create_solid_model` → `acp_update` → `acp_export_solid_model` → `acp_prepare_external_model`. See `examples/build_plate_layup.py`.

### Notes

* The laminate is extruded toward `orientation_point`; a shell at z = 0 with `orientation_point = (0, 0, -1)` puts the plies below the surface.
* `acp_create_layup` names plies `ply03_-45` etc.; `acp_prepare_external_model` reads the fibre angle from that name.
* ACP solid CDBs are structural (layered SOLID185). The converter gives each angle its own material and coordinate system so a coupled-field solve can use them, and runs the result through MAPDL so Workbench's External Model reader accepts it.
* Hexahedral solid models only; composite shell layups, sub-laminates, draping and taper are not wrapped yet.

### Development

```bash
python run_acp_unit_tests.py        # CDB / Workbench-script tests, no ANSYS needed
python examples/stdio_smoke_test.py # start the server over stdio and list tools
```

Integration tests need ACP: set `ACP_MCP_INTEGRATION=1` and `ACP_MCP_SHELL_CDB` to a shell CDB, then run `pytest tests/test_integration.py`.

## License

Apache License 2.0 — see [LICENSE](LICENSE)
