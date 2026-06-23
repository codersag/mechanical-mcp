# ANSYS Mechanical MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that connects any MCP-compatible AI client to ANSYS Mechanical via [PyMechanical](https://mechanical.docs.pyansys.com/) gRPC. Talk to your simulation in plain English.

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
pip install ansys-mechanical-mcp
```

To include DOCX report generation:

```bash
pip install "ansys-mechanical-mcp[report]"
```

Or from source:

```bash
git clone https://github.com/codersag/mechanical-mcp.git
cd mechanical-mcp
pip install -e .
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

**Option A — PyMechanical utility (recommended)**

From any Python terminal with `ansys-mechanical-core` installed:

```python
from ansys.mechanical.core import find_mechanical_instances
print(find_mechanical_instances())
```

This scans for all running Mechanical gRPC servers on the local machine and prints each one's port.

**Option B — Mechanical log file**

Mechanical writes the port to its log when it starts. The log is in your project's `_ProjectScratch` or `SYS` folder:

```
%APPDATA%\Ansys\v232\Mechanical\...
```

Search the log for a line containing `gRPC` or `port`:

```
Mechanical gRPC server started on port 10000
```

**Option C — Workbench scripting console**

In Workbench (`Tools → Scripting → Run Script`):

```python
import wbjn
cmd = 'mechanical.GetServerPort()'
result = wbjn.ExecuteCommand(Services, cmd)
print(result)
```

The default port is **10000**. If you have multiple Mechanical systems open (e.g. Modal + Static Structural), each runs on a different port — run Option A to list them all.

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

## License

Apache License 2.0 — see [LICENSE](LICENSE)
