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

| Category | Capabilities |
|---|---|
| **Connection** | Connect to one or more running Mechanical instances by port |
| **Geometry** | List bodies, assign materials, suppress/unsuppress bodies |
| **Mesh** | Set element size, generate mesh, get statistics |
| **Boundary Conditions** | Fixed support, displacement, remote displacement, frictionless support, force, remote force, pressure, moment, gravity |
| **Solve & Results** | Solve, get status, modal frequencies, equivalent stress, directional deformation, principal stress, stress tool, reaction force |
| **Point Mass** | Convert a Part to a combined Point Mass (no extensions required), auto-create attachment Named Selection |
| **Named Selections** | List, delete |
| **Reports** | Generate DOCX or TXT simulation reports |
| **Scripting** | Run arbitrary IronPython directly in the Mechanical ACT API |

## Requirements

- ANSYS Mechanical 2023 R1 or later (with gRPC enabled)
- Python 3.10+

## Installation

```bash
git clone https://github.com/codersag/mechanical-mcp.git
cd mechanical-mcp
pip install -e .
```

To include DOCX report generation:

```bash
pip install -e ".[report]"
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

1. Open ANSYS Mechanical (gRPC is enabled by default in 2023 R1+)
2. Find the port using one of the methods above
3. Connect to your Mechanical instance:

> "Connect to Mechanical on port 10000"

Then interact naturally:

> "Assign Structural Steel to all bodies"

> "Generate a mesh with 50mm element size"

> "Add a fixed support to the named selection called Base"

> "Solve the analysis and get the maximum von Mises stress"

> "Convert part Equipment1 to a point mass"

## Multiple Instances

You can connect to multiple Mechanical instances (e.g. Modal on port 51811, Static Structural on port 53038) by calling `connect_to_mechanical` with the relevant port. The server maintains one active connection at a time.

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
      "env": { "AWP_ROOT261": "C:\Program Files\ANSYS Inc\v261" }
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
