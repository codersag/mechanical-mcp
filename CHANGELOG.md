# Changelog

All notable changes to this project will be documented in this file.

## [1.4.0]

Fourth audit round, and preparation for publishing the repository.

### Privacy
- New `mcp_paths.py`, shared by the Mechanical and ACP servers: the file allow-list, UNC / alternate
  data stream / device-name refusal, and redaction. Tool replies and errors have the user-profile
  prefix (`%USERPROFILE%`), user name, computer name, licence server / customer / session ids and
  e-mail addresses replaced (`MECHANICAL_MCP_REDACT=0` switches it off). Error messages no longer
  list the allowed folders, only their number.
- The remembered-session file stores the port, a hash of the project folder and the folder's name;
  it no longer stores a path.
- `.gitignore` extended (project files, caches, logs, local configuration); stale script/output
  temp files are cleaned at start-up. `tests/test_privacy.py` checks redaction, the stored state
  and the published files.
- ACP server: every file argument goes through the allow-list (`ACP_MCP_ROOTS`, falling back to
  `MECHANICAL_MCP_ROOTS`), existing outputs need `overwrite=true`, replies are redacted. The README
  JSON example had unescaped backslashes.

### Fixed
- Loads scoped to a body-type named selection: Mechanical leaves such a load without a scope; the
  state is now checked after configuration and the load is rolled back with a clear message.
- Analysis selection: tools that change the model name the analysis they used, require an explicit
  `analysis_index` / `analysis_name` when there are several analyses, and accept
  `expect_analysis_type`.
- Lone surrogates and non-characters in text arguments are refused before any script runs.
- The connection handshake can be abandoned: a port that accepts connections but never speaks gRPC is
  detected by a short protocol probe, and `connect_timeout` bounds the first script.
- Result signedness uses an exact allow-list of signed result types; the export checks for symbolic
  links and junctions through the reparse-point attribute (`os.path.islink` is always false in
  Mechanical's IronPython).
- `add_force`, `add_moment` and `add_remote_force` refuse an all-zero load unless `allow_zero=true`.
- `set_contact_type` skips suppressed regions unless `include_suppressed=true`.
- `delete_named_selection` accepts `object_id`; `list_named_selections` reports ids.
- Point-mass conversion: objects scoped to the converted bodies would lose their scope; they are
  listed (`scope_loss`) and the conversion is refused unless `acknowledge_scope_loss=true`.
- `get_modal_frequencies` flags a partial read (`partial`, `missing_modes`); the all-modes tool
  reuses existing results.
- `linearize_path`: a size step in the mesh is not a gap, a section line shorter than an element is
  refused, a timed-out run is cancelled, and one linearization runs at a time.
- `export_nodal_results`: an analysis with several load steps needs `load_step`; step and time are
  recorded in the file.
- `get_mesh_statistics` says when there is no mesh (`mesh_generated`).

### Tests
- New tests for all of the above, a stricter fake object model, multi-body point-mass numerics
  checked against the parallel-axis theorem, and a mutation run over the new guards.

## [1.3.1]

Third review round.

### Fixed
- `add_contact` reuse compared only the contact type: friction is now part of the identity; a region
  that differs is refused with the differences, or changed with `update_existing=true` (restored on
  failure). Reused contacts and results report `suppressed`.
- `set_contact_type` restores the friction coefficient as well as the type and reports the real
  before/after of both.
- `create_named_selection` refuses body names that match nothing unless `allow_missing=true`.
- `linearize_path`: coincident interface nodes are merged; gaps are judged by comparing simplex
  sizes locally, so 4:1 and 10:1 meshes, graded polar meshes and coarse meshes are no longer flagged
  while real holes and notches still are (`gap_diagnostics`); line ends on a curved boundary or a hair
  outside a flat face are snapped (`snap_fraction`, `endpoint_adjustments`); `component_unverified`,
  direction mismatch (`accept_direction_mismatch`) and `value_unit` that says "unverified" unless the
  export cross-checked it.
- Status tools run on their own pool and at most four calls queue for the Mechanical lock, so the
  "busy within about a second" answer cannot be starved.
- `export_nodal_results` never replaces a file that appeared during the run unless `overwrite=true`;
  node cap and time budget.
- Point-mass duplicate detection matches the exact prefix (collision-safe label with a hash) and the
  "already converted" and "duplicate" paths agree.
- Whole-model results say `scope: "whole model"`; `get_solve_status` names the analysis that is
  solving and validates the index; an unread background result is kept in a history; failed
  handshakes cannot pile up threads; transport failures give one clear line plus `detail`.
- `_call_with_timeout` decides atomically between returning and dropping a late result; no silent
  re-attach to a session without a stored project identity; every tool reports `reconnected_to`;
  busy/timeouts do not count towards forgetting the port; a second Solve after an unfinished one is
  refused; non-ASCII temp paths in the wrapper; reserved Windows device names refused.

## [1.3.0]

Second review round.

### Fixed
- **Crash (exit 139) after any gRPC transport failure**: the connection was dropped inside the
  `except` block, whose traceback kept the object alive, so its channel was closed before the
  finalizer ran. Dropping now happens after leaving the handler (also in `_connect`), and the tests
  fail if the finalize-before-close order is broken or `gc.collect()` is removed.
- `list_point_masses` returned wrong masses in non-SI unit systems (4.008 g reported as 4.008 kg); every
  quantity now goes through `ConvertUnit`. Verified live in MKS, CGS, NMM, BIN and UMKS.
- Point-mass conversion reports `inertia_basis: "principal"` and warnings (principal moments only;
  products of inertia dropped when bodies are combined; faces not aligned with the global axes), and
  refuses a second conversion for the same prefix/part unless `allow_duplicate=true`.
- `check_mechanical_connection`, `get_solve_status` and `disconnect_from_mechanical` answer
  `connected/busy` within about a second while a solve holds Mechanical.
- A failed status poll no longer loses the finished background solve's result; the result is attached
  only to a poll for the same analysis and expires after an hour.
- Connecting to a port that accepts connections but is not Mechanical is bounded (20 s) and does not
  hold the Mechanical lock.
- `delete_named_selection` refuses while objects are scoped to the selection (lists them; `force=true`
  overrides) and deletes every selection of that name.
- `list_boundary_conditions` and the report list only real loads and supports, with scope and values.
- Explicit Dynamics displacements (time tables) are set and read back correctly; `set_nonlinear_settings`
  on an analysis without those settings gives a clear error.
- `set_contact_type` reports only real changes and warns about duplicate region names.
- `export_nodal_results`: the script itself refuses to overwrite, uses a uniquely named exclusive
  temporary file and removes only that; it records `orientation` and whether the value unit was
  cross-checked. `generate_report` no longer touches an unrelated `<name>.part` and publishes without
  replacing a file that appeared meanwhile.
- `linearize_path`: a line crossing a hole or notch is an error (`allow_gaps`), only nodes near the
  line are triangulated (216k nodes: seconds instead of ~38 s), node cap and time budget,
  `expect_component`.
- Reconnecting: check and run are retried if the connection vanished between them, the second
  background solve is refused at once, the remembered port is forgotten only when refused or after
  repeated failures, the project is verified before silently re-attaching and the re-attachment is
  reported as `reconnected_to`, and `disconnect(shutdown=True)` waits for the port to close.

### Added
- Tools: `create_named_selection`, `list_contact_regions`, `list_results`, `get_result_values`.
- `reuse_existing` on the result tools and `add_contact` (identical objects are returned, not duplicated).
- `fully_inside` for box selections; the centroid rule is documented and box selections warn about
  entities that stick out.

## [1.2.0]

Review fixes for the Mechanical server. Only Mechanical 2026 R1 was exercised.

### Fixed
- `add_standard_gravity` called a method that does not exist (`AddStandardEarthGravity`); it now uses `AddEarthGravity`, normalises the direction vector and accepts only +-X/+-Y/+-Z (all Mechanical's Standard Earth Gravity supports).
- Non-ASCII text: tool arguments are embedded as ASCII `\uXXXX` literals and generated scripts run with a unicode-safe `str`/`json.dumps`, so names such as `café_Ø` work in arguments and in the model.
- A failed `run_python_script` is never re-sent (a timed-out solve or `add_*` call was run twice); the channel is dropped and the next call reconnects. The remembered port moved from the shared temp folder to a per-user folder (`MECHANICAL_MCP_STATE_DIR`), the port is probed before connecting, and a working connection is only replaced once the new one answers.
- Results added before a solve return `values: null` and a note instead of `0 [Pa]`; `get_modal_frequencies` and `add_total_deformation_all_modes` refuse static and unsolved analyses.
- Every tool that creates an object removes it again if a later step fails (no more `Force 2` after a failed retry); `add_reaction_force` now works (`LocationMethod = GeometrySelection`, `GeometryLocation`); remote displacement rotations use the real `RotationX/Y/Z` properties; displacement components that start Free can be set.
- Missing or empty named selections, unknown ids, unknown axes and unknown principal-stress kinds are errors and create nothing.
- Numeric arguments are validated (finite, positive/range, size limits) and the real value is read back (`set_mesh_element_size`, `set_body_mesh_size`, loads); `set_2d_behavior` refuses a model without 2D geometry.
- `set_nonlinear_settings` changes only what is passed.
- `generate_report` validates `fmt` and the path first, checks python-docx first, sanitises text, no longer rewrites the extension.
- `export_nodal_results` refuses an unsolved model, no longer depends on a global left by another script, and states the units it wrote.
- Point-mass conversion measures first and changes last, undoes everything on failure and no longer suppresses bodies before the face search; an empty prefix is refused.
- `assign_material`, `set_body_mesh_size` and `suppress_bodies` act on every same-named body; the material is validated against Engineering Data; `set_body_mesh_size` updates its own sizing control instead of adding another; `set_contact_type` can be limited (`region_names`, `only_if_currently`).
- `linearize_path`: every failure is a clear `ok:false`; 3D points and a slice coordinate; unsigned quantities refused; `peak` is total minus (membrane + bending); `thickness_mm` renamed `section_length_mm`.
- Solve: `ok` needs status Done, the Messages pane is no longer cleared, `solve_analysis(wait=False)` runs the solve on a background thread (Mechanical's local solve refuses `wait=False`), `get_solve_status` reports `Solving`, filters the grpc start-up notice.

### Changed
- Tools run on worker threads with a bounded lock wait instead of blocking the event loop.
- File outputs/inputs are confined to `MECHANICAL_MCP_ROOTS` (default: the working directory), network paths, `:` streams and wrong extensions are refused, and nothing is overwritten without `overwrite=true`.
- `run_mechanical_script` is off unless `MECHANICAL_MCP_ENABLE_SCRIPT=1`.
- `disconnect_from_mechanical(shutdown=True)` needs `confirm="SHUT DOWN MECHANICAL"`.
- `suppress_bodies` needs a prefix (or `all_bodies=true`); a prefix matching nothing is an error.
- Result JSON is flat `{"ok": ..., ...}` everywhere (`get_model_info`, `get_mesh_statistics` no longer nest under a key).
- Version is 1.2.0 in both `pyproject.toml` and `__version__`.

### Added
- A real test suite (no Mechanical needed) with a fake object model, hostile-string tests for every tool, and `pytest`/`ruff` configured at the repo root.

## [Unreleased]

### Added (Mechanical server)
- **Geometry-based load locations**: `add_fixed_support`, `add_frictionless_support`, `add_displacement`, `add_force` and `add_pressure` accept `entity_ids` or `box_mm` (with `entity_type`, `body_name`) instead of a named selection.
- **Model setup**: `set_2d_behavior`, `set_body_mesh_size`, `set_nonlinear_settings`, `set_contact_type`, `add_contact`.
- **Results**: `add_normal_stress`, `add_stress_intensity`, `export_nodal_results`, `linearize_path` (needs the `postproc` extra).
- `prepare_external_model` accepts `node_boxes_json` to create custom node components instead of STRIKE / GNDNODES.
- Script builders moved to `mechanical_scripts.py` and `mechanical_postproc.py`, with unit tests that need no Mechanical session.

### Added
- **ACP server** (`ansys-acp-mcp`): PyACP-based tools to launch ACP, import shell meshes, define materials/fabrics/rosettes/oriented selection sets, build layups and solid models, export and convert CDBs for Mechanical, and generate Workbench ACP scripts. Install with the `acp` extra.

## [1.0.0] - 2026-06-23

### Added
- **Connection management**: `connect_to_mechanical`, `disconnect_from_mechanical`, `check_mechanical_connection`
- **Model info**: `get_model_info`, `list_materials`, `assign_material`
- **Mesh**: `set_mesh_element_size`, `generate_mesh`, `get_mesh_statistics`
- **Boundary conditions**: `add_fixed_support`, `add_frictionless_support`, `add_displacement`, `add_remote_displacement`, `add_force`, `add_remote_force`, `add_pressure`, `add_moment`, `add_standard_gravity`
- **Solve**: `solve_analysis`, `get_solve_status`
- **Results**: `add_equivalent_stress`, `add_principal_stress`, `add_stress_tool`, `add_total_deformation`, `add_total_deformation_all_modes`, `add_directional_deformation`, `get_modal_frequencies`, `add_reaction_force`
- **Geometry**: `suppress_bodies`, `list_point_masses`
- **Point mass conversion** (no ACT extensions required): `convert_part_to_point_mass`, `convert_prefix_to_point_mass`
- **Named selections**: `list_named_selections`, `delete_named_selection`
- **Reports**: `generate_report` (DOCX and TXT)
- **Scripting**: `run_mechanical_script`
