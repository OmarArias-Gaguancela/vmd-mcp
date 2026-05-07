# vmd-mcp: Plain Language Control of VMD2 via Claude MCP

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows-informational?logo=windows)

**Author:** Omar Arias-Gaguancela, PhD  
**Affiliations:** SciLearningWorkshops LLC

---

## What Is vmd-mcp?

`vmd-mcp` is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives Claude direct, programmatic control over **VMD2** (Visual Molecular Dynamics). Instead of writing Tcl scripts or navigating VMD's GUI, you describe what you want in plain English and Claude handles the rest — loading structures, setting up representations, measuring distances, computing RMSD, rendering images, and more.

### Why It Matters for Computational Biologists

Molecular dynamics analysis is a bottleneck: generating the simulation is increasingly automated, but *interpreting* it still demands expert knowledge of Tcl scripting, VMD's GUI, and command-line tooling. `vmd-mcp` closes that gap.

With this tool, a researcher can go from trajectory file to publication-quality figures and quantitative measurements in a single conversation — no Tcl knowledge required. It also lowers the barrier for trainees and students entering the field, letting them focus on biology rather than software syntax. The security-conscious design (path allowlists, Tcl command validation) makes it safe to deploy in shared or instructional computing environments.

---

## Key Features

- **23 MCP tools** covering structure loading, trajectory navigation, representations, measurements, rendering, and Tcl passthrough
- **Natural language interface** — ask Claude to visualize a protein, measure a distance, or compute RMSD in plain English
- **Subprocess mode** — launches and controls VMD2 headlessly on Windows with no manual Tcl scripting needed
- **Security-first design** — all file paths are validated against a configurable allowlist; Tcl commands are sanitized to block dangerous operations (`exec`, `open`, `file`, `socket`, etc.)
- **Windows-native** — includes the `CREATE_NO_WINDOW` subprocess fix so no stray console windows appear during operation
- **Socket mode** — optionally connect to a pre-running VMD instance on `localhost:5555`
- **Mock VMD server** — test the full pipeline without VMD installed using `mock_vmd.py`
- **MCP Resources** — Claude can query live VMD state (`vmd://status`, `vmd://molecules`, `vmd://selections-guide`) before issuing commands

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| [VMD2](https://www.ks.uiuc.edu/Research/vmd/) | 2.x | Install to default path or update `vmd_mcp_config.json` |
| Python | 3.11+ | Required for `asyncio` subprocess features used on Windows |
| [Claude Code](https://claude.ai/code) | Latest | CLI, desktop app, or IDE extension |
| `mcp` | ≥ 1.0.0 | Model Context Protocol SDK |
| `pydantic` | ≥ 2.0.0 | Data validation for tool inputs |

---

## Installation

### 1. Clone the Repository

```powershell
git clone https://github.com/oarias/vmd-mcp.git
cd vmd-mcp
```

### 2. Create a Virtual Environment (Recommended)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 4. Verify VMD2 Is Installed

The server expects VMD2 at:

```
C:\Program Files\University of Illinois\VMD2\vmd.exe
```

If your installation path differs, open `vmd_mcp_config.json` and update `vmd_executable`:

```json
{
  "vmd_executable": "C:\\Program Files\\University of Illinois\\VMD2\\vmd.exe",
  "communication_mode": "subprocess",
  "socket_host": "localhost",
  "socket_port": 5555,
  "socket_timeout": 10.0,
  "subprocess_args": ["-dispdev", "win"],
  "allowed_directories": [
    "C:\\Users\\YourName\\Desktop",
    "C:\\Users\\YourName\\Documents",
    "C:\\Users\\YourName\\Downloads"
  ]
}
```

> **Security note:** Only files inside `allowed_directories` can be loaded into VMD. Add any additional data directories you need.

---

## Registering vmd-mcp with Claude Code

### Option A — Claude Code CLI

```powershell
claude mcp add vmd-mcp `
  --command "C:\Users\YourName\Desktop\vmd-mcp\.venv\Scripts\python.exe" `
  --args "C:\Users\YourName\Desktop\vmd-mcp\vmd_mcp_server.py"
```

### Option B — Manual Config

Edit `%APPDATA%\Claude\claude_desktop_config.json` (desktop app) or `.claude.json` (CLI):

```json
{
  "mcpServers": {
    "vmd-mcp": {
      "command": "C:\\Users\\YourName\\Desktop\\vmd-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YourName\\Desktop\\vmd-mcp\\vmd_mcp_server.py"],
      "cwd": "C:\\Users\\YourName\\Desktop\\vmd-mcp"
    }
  }
}
```

Restart Claude Code after saving. You should see `vmd-mcp` listed under connected MCP servers.

### Verify the Connection

Ask Claude: *"What MCP tools are available for VMD?"*  
Claude will enumerate all 23 tools if the server is connected correctly.

---

## Usage — Example Natural Language Prompts

### Loading Structures and Trajectories

```
Load C:\Users\me\Desktop\project\protein.pdb into VMD.
```

```
Load MtFAAH_1MAG16.pdb as the topology and MtFAAH_1MAG16_10ns_merged.dcd
as the trajectory.
```

### Visualization

```
Show the protein as NewCartoon colored by secondary structure,
and display the ligand as Licorice with CPK coloring.
```

```
Set the background to white and reset the camera.
```

```
Add a VDW surface for all water molecules within 5 Å of the active site.
```

### Trajectory Navigation

```
How many frames are in molecule 0?
```

```
Go to frame 500 and take a snapshot — save it to C:\Users\me\Desktop\frame500.tga.
```

```
Play the trajectory from frame 0 to 1000 at speed 5.
```

### Measurements

```
Measure the distance between atom 1042 and atom 2318 in the current frame.
```

```
What is the angle formed by atoms 101, 205, and 310?
```

```
Compute the backbone RMSD relative to frame 0 across all frames.
```

### Atom Information

```
What residue and atom name is atom index 4711?
```

```
List all atoms in the selection "resname MAG" — show residue IDs and atom names.
```

### Rendering and Saving

```
Render a high-quality Tachyon image to C:\Users\me\Desktop\figure1.tga.
```

```
Save the current VMD session to C:\Users\me\Desktop\session.vmd.
```

### Quick Reference — All Tools

| Natural language | Tool |
|---|---|
| "Load a PDB file" | `load_structure` |
| "Load topology + trajectory" | `load_trajectory` |
| "What molecules are loaded?" | `list_loaded_molecules` |
| "Remove molecule 2" | `delete_molecule` |
| "Set the representation to NewCartoon" | `set_representation` |
| "Add a Licorice layer" | `add_representation` |
| "Remove representation 0" | `delete_representation` |
| "Change background color" | `set_background_color` |
| "Reset the view" | `reset_view` |
| "How many frames?" | `get_frame_count` |
| "Jump to frame N" | `go_to_frame` |
| "Play the trajectory" | `play_trajectory` |
| "Stop the animation" | `stop_playback` |
| "Measure distance between two atoms" | `measure_distance` |
| "Measure angle at three atoms" | `measure_angle` |
| "Compute RMSD" | `measure_rmsd` |
| "Show atom info for a selection" | `get_atom_info` |
| "Render an image" | `render_image` |
| "Save the VMD session" | `save_session` |
| "Run this Tcl command" | `execute_tcl` |

---

## VMD Atom Selection Quick Reference

```
protein                        all protein atoms
backbone                       N, CA, C, O atoms
resname LIG                    residue named LIG
resid 45                       residue number 45
resid 45 to 102                residue range
chain A                        chain A
name CA                        alpha carbons only
within 5 of resname LIG        atoms within 5 Å of ligand
protein and not water          protein excluding solvent
```

---

## Troubleshooting

### VMD Does Not Launch

**Cause:** `vmd_executable` path in `vmd_mcp_config.json` is incorrect.  
**Fix:** Confirm the path by running VMD directly:

```powershell
& "C:\Program Files\University of Illinois\VMD2\vmd.exe" -dispdev none -e nul
```

If VMD exits without error, the path is valid. Update `vmd_executable` in the config if needed.

---

### Console Window Flashes on Every Command

**Cause:** Missing `CREATE_NO_WINDOW` subprocess flag.  
**Fix:** This is already handled in `vmd_controller.py`:

```python
if hasattr(subprocess, "CREATE_NO_WINDOW"):
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
```

If you still see flashing windows, ensure you are running Python 3.7+ and have not modified this block. The flag suppresses the hidden console Windows creates for subprocess children by default.

---

### `asyncio` Event Loop Error on Windows (`ProactorEventLoop`)

**Cause:** Python 3.8+ on Windows defaults to `ProactorEventLoop`, which conflicts with certain subprocess pipe operations used by the MCP SDK.  
**Fix:** The following policy is already set at startup in `vmd_mcp_server.py`:

```python
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

If you encounter this error after modifying the server, verify this policy is set before `asyncio.run()`.

---

### `Path outside allowed directories` Error

**Cause:** The file you are trying to load is not in `allowed_directories`.  
**Fix:** Add the directory to `vmd_mcp_config.json`:

```json
"allowed_directories": [
  "C:\\Users\\YourName\\Desktop",
  "C:\\Users\\YourName\\Projects\\my_md_data"
]
```

---

### MCP Server Not Appearing in Claude Code

1. Confirm the `command` path in the MCP config points to the `.venv` Python interpreter, not the system Python.
2. Run the server manually to check for import errors:

   ```powershell
   .venv\Scripts\python.exe vmd_mcp_server.py
   ```

3. Restart Claude Code completely after any configuration change.

---

### Tcl Command Blocked by Security Validator

`execute_tcl` blocks the following commands by design: `exec`, `open`, `file`, `socket`, `package`, `source`, `load`, `unload`, `exit`, `quit`, `proc`, `namespace`, `interp`, `vwait`. Use the purpose-built tools (`load_structure`, `render_image`, etc.) for these operations instead.

---

### Testing Without VMD — Mock Server

```powershell
# Terminal 1 — start the mock VMD server
python mock_vmd.py

# Terminal 2 — start the MCP server (connects to the mock)
python vmd_mcp_server.py
```

The mock server listens on port 5555 and returns realistic canned responses (e.g., `DISTANCE: 3.8245`, `NUMFRAMES: 500`), allowing full pipeline testing without a VMD installation.

---

## Citation

If you use `vmd-mcp` in published research or educational materials, please cite:

```bibtex
@software{arias2026vmdmcp,
  author    = {Arias-Gaguancela, Omar},
  title     = {vmd-mcp: Plain Language Control of VMD2 via Claude MCP},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/oarias/vmd-mcp},
  doi       = {10.5281/zenodo.XXXXXXX}
}
```

> The DOI will be registered upon first stable release. Check the repository for the current citation record.

Also cite VMD:

> Humphrey, W., Dalke, A. and Schulten, K. (1996). VMD — Visual Molecular Dynamics. *Journal of Molecular Graphics*, **14**, 33–38. https://doi.org/10.1016/0263-7855(96)00018-5

---

## License

MIT License

Copyright (c) 2026 Omar Arias-Gaguancela

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

---

## About

**Omar Arias-Gaguancela, PhD** is a computational biologist and science educator focused on making advanced molecular simulation tools accessible to researchers at all career stages.

- **SciLearningWorkshops LLC** — Workshops and training in structural biology, MD simulation, and AI-assisted research

---

*Built with the [Model Context Protocol](https://modelcontextprotocol.io) · Powered by [Claude](https://claude.ai)*
