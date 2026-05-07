"""
vmd_mcp_server.py — MCP server that exposes VMD2 control tools to Claude.

Start with:
    python vmd_mcp_server.py

The server communicates over stdio (standard MCP transport).
VMD must already be running with its Tcl socket server enabled on the
configured port (default 5555).  See README.md for details.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    TextContent,
    Tool,
)
import mcp.types as types

from vmd_controller import VMDSocketController, make_controller, load_config, validate_path
from tcl_builder import (
    validate_tcl_command,
    load_mol,
    load_trajectory as tcl_load_trajectory,
    delete_molecule as tcl_delete_molecule,
    list_molecules,
    set_representation as tcl_set_rep,
    add_representation as tcl_add_rep,
    delete_representation as tcl_delete_rep,
    set_background_color as tcl_bg_color,
    reset_view as tcl_reset_view,
    get_frame_count as tcl_frame_count,
    go_to_frame as tcl_goto_frame,
    play_trajectory as tcl_play,
    stop_playback as tcl_stop,
    measure_distance as tcl_distance,
    measure_angle as tcl_angle,
    measure_rmsd as tcl_rmsd,
    get_atom_info as tcl_atom_info,
    render_image as tcl_render,
    save_session as tcl_save_session,
    vmd_version,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vmd-mcp")

# ---------------------------------------------------------------------------
# Global controller instance (lazy-connected)
# ---------------------------------------------------------------------------

_config = load_config()
_controller: VMDSocketController = make_controller()

app = Server("vmd-mcp")


# ---------------------------------------------------------------------------
# Helper: run a Tcl command and return result or raise
# ---------------------------------------------------------------------------

def _run(tcl: str) -> str:
    try:
        return _controller.send_safe(tcl)
    except ConnectionError as exc:
        raise RuntimeError(
            f"VMD is not reachable: {exc}\n\n"
            "To start VMD with its Tcl server, run:\n"
            r'  "C:\Program Files\University of Illinois\VMD\vmd.exe"'
            " -dispdev win\n"
            "Then in VMD's Tk console run:\n"
            "  package require Tcl\n"
            "  socket -server accept 5555\n"
            "Or see README.md for the full setup guide."
        ) from exc


def _ok(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"ERROR: {msg}")]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="load_structure",
            description=(
                "Load a molecular structure file into VMD. "
                "Supports PDB, PSF, GRO, MOL2, XYZ, and other VMD-compatible formats. "
                "Returns the molecule ID assigned by VMD."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Full Windows path to the structure file, e.g. C:\\Users\\foo\\protein.pdb",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "File format: pdb, psf, gro, mol2, xyz, crd, etc.",
                        "default": "pdb",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="load_trajectory",
            description=(
                "Load a topology file together with a trajectory (DCD, XTC, TRR, etc.). "
                "The topology provides atom names/residues; the trajectory provides coordinates over time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topology_path": {
                        "type": "string",
                        "description": "Full path to the topology file (PSF, GRO, PDB, …).",
                    },
                    "trajectory_path": {
                        "type": "string",
                        "description": "Full path to the trajectory file (DCD, XTC, TRR, …).",
                    },
                    "traj_type": {
                        "type": "string",
                        "description": "Trajectory format: dcd, xtc, trr, coor, etc.",
                        "default": "dcd",
                    },
                },
                "required": ["topology_path", "trajectory_path"],
            },
        ),
        Tool(
            name="list_loaded_molecules",
            description="Return all molecule IDs currently loaded in VMD along with their names and frame counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="delete_molecule",
            description="Remove a molecule from VMD by its molecule ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer", "description": "The VMD molecule ID to delete."},
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="set_representation",
            description=(
                "Replace all visual representations of a molecule with a single new one. "
                "style can be: NewCartoon, Licorice, VDW, Ribbons, Surf, QuickSurf, Lines, Bonds, CPK, etc. "
                "selection uses VMD atom selection language, e.g. 'protein', 'resname LIG', 'backbone', 'all'. "
                "color_method: Name, Element, ResType, ResID, Beta, Occupancy, ColorID 0, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "style": {"type": "string", "default": "NewCartoon"},
                    "selection": {"type": "string", "default": "all"},
                    "color_method": {"type": "string", "default": "Name"},
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="add_representation",
            description=(
                "Add a new visual representation to a molecule WITHOUT removing existing ones. "
                "Useful to show, e.g., the protein as cartoon AND the ligand as licorice simultaneously."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "style": {"type": "string", "default": "Licorice"},
                    "selection": {"type": "string", "default": "all"},
                    "color_method": {"type": "string", "default": "Element"},
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="delete_representation",
            description="Remove a specific representation (by its index) from a molecule.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "rep_id": {"type": "integer", "description": "Zero-based representation index."},
                },
                "required": ["mol_id", "rep_id"],
            },
        ),
        Tool(
            name="set_background_color",
            description="Set the VMD background color, e.g. black, white, gray, blue.",
            inputSchema={
                "type": "object",
                "properties": {
                    "color": {"type": "string", "description": "VMD color name such as black, white, gray, blue."},
                },
                "required": ["color"],
            },
        ),
        Tool(
            name="reset_view",
            description="Center all loaded molecules and reset the camera to the default view.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_frame_count",
            description="Return the number of trajectory frames loaded for a given molecule.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="go_to_frame",
            description="Jump the trajectory of a molecule to a specific frame number.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "frame_number": {"type": "integer", "description": "Zero-based frame index."},
                },
                "required": ["mol_id", "frame_number"],
            },
        ),
        Tool(
            name="play_trajectory",
            description="Animate the trajectory of a molecule between two frames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "start": {"type": "integer", "default": 0},
                    "end": {"type": "integer", "default": -1, "description": "-1 means last frame."},
                    "step": {"type": "integer", "default": 1},
                    "speed": {"type": "integer", "default": 1, "description": "Playback speed multiplier (1–10)."},
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="stop_playback",
            description="Stop any currently running trajectory animation in VMD.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="measure_distance",
            description=(
                "Measure the distance in Ångströms between two atoms specified by their "
                "zero-based atom indices."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "atom1_index": {"type": "integer"},
                    "atom2_index": {"type": "integer"},
                },
                "required": ["mol_id", "atom1_index", "atom2_index"],
            },
        ),
        Tool(
            name="measure_angle",
            description="Measure the angle in degrees formed by three atoms (specified by atom indices).",
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "atom1": {"type": "integer"},
                    "atom2": {"type": "integer", "description": "The vertex atom."},
                    "atom3": {"type": "integer"},
                },
                "required": ["mol_id", "atom1", "atom2", "atom3"],
            },
        ),
        Tool(
            name="measure_rmsd",
            description=(
                "Compute the RMSD (root mean square deviation) in Ångströms for a given "
                "atom selection relative to a reference frame."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "selection": {
                        "type": "string",
                        "default": "backbone",
                        "description": "VMD atom selection, e.g. 'backbone', 'protein and name CA'.",
                    },
                    "ref_frame": {
                        "type": "integer",
                        "default": 0,
                        "description": "Reference frame index (0 = first frame).",
                    },
                },
                "required": ["mol_id"],
            },
        ),
        Tool(
            name="get_atom_info",
            description=(
                "Return residue names, residue IDs, atom names, and indices for atoms matching "
                "the given VMD selection string."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mol_id": {"type": "integer"},
                    "selection": {
                        "type": "string",
                        "description": "VMD atom selection, e.g. 'resid 45', 'resname ATP', 'protein and backbone'.",
                    },
                },
                "required": ["mol_id", "selection"],
            },
        ),
        Tool(
            name="render_image",
            description=(
                "Render the current VMD viewport to an image file. "
                "renderer: snapshot (fast OpenGL), Tachyon (ray-traced), TachyonInternal, OptiX. "
                "output_path should end with .ppm, .tga, or .png depending on the renderer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output_path": {"type": "string", "description": "Full Windows path for the output image."},
                    "renderer": {"type": "string", "default": "snapshot"},
                    "width": {"type": "integer", "default": 1920},
                    "height": {"type": "integer", "default": 1080},
                },
                "required": ["output_path"],
            },
        ),
        Tool(
            name="save_session",
            description="Save the current VMD state (all molecules, representations, settings) to a .vmd file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "output_path": {"type": "string", "description": "Full Windows path for the .vmd state file."},
                },
                "required": ["output_path"],
            },
        ),
        Tool(
            name="execute_tcl",
            description=(
                "Send a raw Tcl command directly to VMD. "
                "Use this as an escape hatch when no other tool covers your need. "
                "Dangerous commands (exec, file, open, exit, etc.) are blocked."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tcl_command": {
                        "type": "string",
                        "description": "A single Tcl statement to run in VMD's interpreter.",
                    },
                },
                "required": ["tcl_command"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool call handler
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        return await _dispatch(name, arguments)
    except FileNotFoundError as exc:
        return _err(str(exc))
    except ValueError as exc:
        return _err(str(exc))
    except RuntimeError as exc:
        return _err(str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in tool '%s'", name)
        return _err(f"Unexpected error: {exc}")


async def _dispatch(name: str, args: dict[str, Any]) -> list[TextContent]:
    # ---- Structure loading ------------------------------------------------
    if name == "load_structure":
        fp = args["file_path"]
        validate_path(fp, _config)
        ft = args.get("file_type", "pdb")
        result = _run(load_mol(fp, ft))
        return _ok(f"Loaded structure.\n{result}")

    if name == "load_trajectory":
        tp = args["topology_path"]
        trp = args["trajectory_path"]
        validate_path(tp, _config)
        validate_path(trp, _config)
        tt = args.get("traj_type", "dcd")
        result = _run(tcl_load_trajectory(tp, trp, tt))
        return _ok(f"Loaded trajectory.\n{result}")

    if name == "list_loaded_molecules":
        result = _run(list_molecules())
        return _ok(result if result else "No molecules loaded.")

    if name == "delete_molecule":
        result = _run(tcl_delete_molecule(args["mol_id"]))
        return _ok(f"Deleted molecule {args['mol_id']}.\n{result}")

    # ---- Representations --------------------------------------------------
    if name == "set_representation":
        mid = args["mol_id"]
        result = _run(
            tcl_set_rep(
                mid,
                args.get("style", "NewCartoon"),
                args.get("selection", "all"),
                args.get("color_method", "Name"),
            )
        )
        return _ok(f"Representation set.\n{result}")

    if name == "add_representation":
        mid = args["mol_id"]
        result = _run(
            tcl_add_rep(
                mid,
                args.get("style", "Licorice"),
                args.get("selection", "all"),
                args.get("color_method", "Element"),
            )
        )
        return _ok(f"Representation added.\n{result}")

    if name == "delete_representation":
        result = _run(tcl_delete_rep(args["mol_id"], args["rep_id"]))
        return _ok(f"Representation {args['rep_id']} deleted.")

    if name == "set_background_color":
        result = _run(tcl_bg_color(args["color"]))
        return _ok(f"Background set to {args['color']}.")

    if name == "reset_view":
        result = _run(tcl_reset_view())
        return _ok("View reset.")

    # ---- Trajectory -------------------------------------------------------
    if name == "get_frame_count":
        result = _run(tcl_frame_count(args["mol_id"]))
        return _ok(result)

    if name == "go_to_frame":
        _run(tcl_goto_frame(args["mol_id"], args["frame_number"]))
        return _ok(f"Jumped to frame {args['frame_number']}.")

    if name == "play_trajectory":
        mid = args["mol_id"]
        end = args.get("end", -1)
        if end == -1:
            nf_raw = _run(tcl_frame_count(mid))
            # parse "NUMFRAMES: N"
            try:
                end = int(nf_raw.split(":")[-1].strip()) - 1
            except (ValueError, IndexError):
                end = 9999
        result = _run(
            tcl_play(
                mid,
                args.get("start", 0),
                end,
                args.get("step", 1),
                args.get("speed", 1),
            )
        )
        return _ok(f"Playback started.\n{result}")

    if name == "stop_playback":
        _run(tcl_stop())
        return _ok("Playback stopped.")

    # ---- Analysis ---------------------------------------------------------
    if name == "measure_distance":
        result = _run(
            tcl_distance(args["mol_id"], args["atom1_index"], args["atom2_index"])
        )
        return _ok(result)

    if name == "measure_angle":
        result = _run(
            tcl_angle(args["mol_id"], args["atom1"], args["atom2"], args["atom3"])
        )
        return _ok(result)

    if name == "measure_rmsd":
        result = _run(
            tcl_rmsd(
                args["mol_id"],
                args.get("selection", "backbone"),
                args.get("ref_frame", 0),
            )
        )
        return _ok(result)

    if name == "get_atom_info":
        result = _run(tcl_atom_info(args["mol_id"], args["selection"]))
        return _ok(result)

    # ---- Rendering --------------------------------------------------------
    if name == "render_image":
        op = args["output_path"]
        result = _run(
            tcl_render(
                op,
                args.get("renderer", "snapshot"),
                args.get("width", 1920),
                args.get("height", 1080),
            )
        )
        return _ok(f"Image rendered.\n{result}")

    if name == "save_session":
        result = _run(tcl_save_session(args["output_path"]))
        return _ok(f"Session saved.\n{result}")

    # ---- Tcl passthrough --------------------------------------------------
    if name == "execute_tcl":
        cmd = args["tcl_command"]
        validate_tcl_command(cmd)
        result = _run(cmd)
        return _ok(result)

    return _err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@app.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="vmd://status",
            name="VMD Connection Status",
            description="Current VMD connection status, version, and loaded molecule count.",
            mimeType="text/plain",
        ),
        Resource(
            uri="vmd://molecules",
            name="Loaded Molecules",
            description="Full list of molecules currently loaded in VMD with metadata.",
            mimeType="text/plain",
        ),
        Resource(
            uri="vmd://selections-guide",
            name="VMD Atom Selection Reference",
            description=(
                "Quick reference for VMD atom selection syntax. "
                "Use this when building selection strings for representation or analysis tools."
            ),
            mimeType="text/plain",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    if uri == "vmd://status":
        if not _controller.connected:
            try:
                _controller.connect()
            except ConnectionError as exc:
                return f"DISCONNECTED\n{exc}"
        try:
            ver = _run(vmd_version())
            mols_raw = _run(list_molecules())
            mol_count = mols_raw.count("MOL:") if mols_raw else 0
            return (
                f"STATUS: Connected\n"
                f"{ver}\n"
                f"Loaded molecules: {mol_count}\n"
                f"Socket: {_controller.host}:{_controller.port}"
            )
        except Exception as exc:
            return f"ERROR: {exc}"

    if uri == "vmd://molecules":
        try:
            result = _run(list_molecules())
            return result if result else "No molecules loaded."
        except Exception as exc:
            return f"ERROR: {exc}"

    if uri == "vmd://selections-guide":
        return _SELECTIONS_GUIDE

    raise ValueError(f"Unknown resource URI: {uri}")


_SELECTIONS_GUIDE = """\
VMD Atom Selection Language — Quick Reference
=============================================

KEYWORD SELECTIONS
  all                  — every atom
  protein              — all protein atoms
  nucleic              — DNA/RNA atoms
  backbone             — protein backbone (N, CA, C, O)
  sidechain            — protein side chains
  water                — water molecules
  lipid                — lipid atoms
  ions                 — ionic atoms

RESIDUE / CHAIN
  resname LIG          — atoms in residue named LIG
  resname LIG ATP      — multiple residue names (space-separated)
  resid 45             — residue number 45
  resid 45 to 102      — residue range
  chain A              — chain A only
  segname SEG1         — by segment name (NAMD/CHARMM)

ATOM PROPERTIES
  name CA              — alpha-carbon atoms
  name CA CB           — multiple atom names
  index 0              — atom at index 0 (zero-based)
  index 0 to 99        — atom index range
  element C            — all carbon atoms

BOOLEAN OPERATORS
  protein and name CA           — Cα atoms only
  resname LIG or resname HEM    — two residues
  not water                     — everything except water
  protein and not backbone      — side chains only

DISTANCE-BASED
  within 5 of resname LIG    — atoms within 5Å of the ligand
  exwithin 3 of protein      — atoms within 3Å of protein, excluding protein

NUMERIC COMPARISONS
  beta > 0.5           — atoms where B-factor > 0.5
  occupancy == 1.0     — full occupancy atoms
  mass < 14            — light atoms (H, C roughly)

MACROS (VMD built-in)
  at, cg, pu, py       — purine/pyrimidine bases
  acidic, basic, polar, hydrophobic  — residue property macros
  charged              — charged residues

EXAMPLES
  "protein and backbone"
  "resname LIG"
  "protein and resid 45 to 60"
  "within 5 of resname ATP"
  "protein and not water and name CA"
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
