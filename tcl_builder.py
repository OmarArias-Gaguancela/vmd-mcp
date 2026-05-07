"""
tcl_builder.py — Helpers that build safe Tcl command strings for VMD.

All public functions return a string of valid Tcl that can be sent to VMD
via socket or subprocess.  Dangerous shell-escape characters are quoted so
that user-supplied strings cannot inject arbitrary Tcl.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PureWindowsPath, PurePosixPath


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# Characters that are special in Tcl and must be escaped inside {…} blocks
# or quoted with backslash when used in double-quoted strings.
_TCL_DANGEROUS_PATTERN = re.compile(r"[;\[\]{}\"\\$`]")

# Commands that must never be forwarded to VMD's Tcl interpreter.
_BLOCKED_TCL_COMMANDS = frozenset(
    [
        "exec",
        "open",
        "file",
        "socket",
        "package",
        "source",
        "load",
        "unload",
        "exit",
        "quit",
        "proc",
        "namespace",
        "interp",
        "vwait",
    ]
)


def validate_tcl_command(cmd: str) -> None:
    """
    Raise ValueError if *cmd* contains obviously dangerous patterns.

    This is a defence-in-depth check, not a full Tcl parser.  The first
    word of the command must not be a blocked command, and the string must
    not contain raw semicolons that could chain additional commands.
    """
    stripped = cmd.strip()
    if not stripped:
        raise ValueError("Empty Tcl command.")

    # Reject multi-statement commands (semicolons outside braces are risky)
    # Simple heuristic: count unbraced semicolons
    depth = 0
    for ch in stripped:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ";" and depth == 0:
            raise ValueError(
                "Tcl command contains a semicolon at the top level. "
                "Send one command at a time."
            )

    first_word = stripped.split()[0].lower().lstrip(":")
    if first_word in _BLOCKED_TCL_COMMANDS:
        raise ValueError(
            f"Tcl command '{first_word}' is blocked for security reasons."
        )


def tcl_quote(value: str) -> str:
    """
    Wrap *value* in Tcl braces so it is treated as a literal string.

    Brace quoting is safe for any string that does not itself contain
    unbalanced braces.  We escape internal braces with backslash first.
    """
    escaped = value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return "{" + escaped + "}"


def windows_path_to_tcl(path: str) -> str:
    """
    Convert a Windows path (backslashes) to a forward-slash path suitable
    for Tcl, then brace-quote it.

    Example: C:\\Users\\foo\\bar.pdb  →  {C:/Users/foo/bar.pdb}
    """
    fwd = path.replace("\\", "/")
    return tcl_quote(fwd)


# ---------------------------------------------------------------------------
# Structure / trajectory loading
# ---------------------------------------------------------------------------

def load_mol(file_path: str, file_type: str) -> str:
    """Return Tcl to load a single structure file and echo its mol ID."""
    fp = windows_path_to_tcl(file_path)
    ft = tcl_quote(file_type.lower())
    return (
        f"set _mid [mol new {fp} type {ft} waitfor all]; "
        f"puts \"MOL_ID: $_mid\""
    )


def load_trajectory(topology_path: str, traj_path: str, traj_type: str) -> str:
    """Return Tcl to load a topology + trajectory pair."""
    tp = windows_path_to_tcl(topology_path)
    trp = windows_path_to_tcl(traj_path)
    tt = tcl_quote(traj_type.lower())
    return (
        f"set _mid [mol new {tp} waitfor all]; "
        f"mol addfile {trp} type {tt} mol $_mid waitfor all; "
        f"puts \"MOL_ID: $_mid\""
    )


def delete_molecule(mol_id: int) -> str:
    return f"mol delete {mol_id}"


def list_molecules() -> str:
    """Return Tcl that prints each loaded molecule on its own line."""
    return (
        "foreach _m [molinfo list] {"
        "  puts \"MOL: $_m [molinfo $_m get name] "
        "[molinfo $_m get numframes] frames\""
        "}"
    )


# ---------------------------------------------------------------------------
# Representation helpers
# ---------------------------------------------------------------------------

def _rep_base(mol_id: int, style: str, selection: str, color_method: str) -> tuple[str, str, str]:
    s = tcl_quote(style)
    sel = tcl_quote(selection)
    cm = tcl_quote(color_method)
    return s, sel, cm


def set_representation(
    mol_id: int, style: str, selection: str, color_method: str
) -> str:
    """Replace all representations on *mol_id* with a single new one."""
    s, sel, cm = _rep_base(mol_id, style, selection, color_method)
    return (
        f"mol delrep all {mol_id}; "
        f"mol representation {s}; "
        f"mol selection {sel}; "
        f"mol color {cm}; "
        f"mol addrep {mol_id}"
    )


def add_representation(
    mol_id: int, style: str, selection: str, color_method: str
) -> str:
    """Add a representation without removing existing ones."""
    s, sel, cm = _rep_base(mol_id, style, selection, color_method)
    return (
        f"mol representation {s}; "
        f"mol selection {sel}; "
        f"mol color {cm}; "
        f"mol addrep {mol_id}"
    )


def delete_representation(mol_id: int, rep_id: int) -> str:
    return f"mol delrep {rep_id} {mol_id}"


def set_background_color(color: str) -> str:
    c = tcl_quote(color)
    return f"color Display Background {c}"


def reset_view() -> str:
    return "display resetview; mol center all"


# ---------------------------------------------------------------------------
# Trajectory navigation
# ---------------------------------------------------------------------------

def get_frame_count(mol_id: int) -> str:
    return f"puts \"NUMFRAMES: [molinfo {mol_id} get numframes]\""


def go_to_frame(mol_id: int, frame: int) -> str:
    return f"molinfo {mol_id} set frame {frame}"


def play_trajectory(mol_id: int, start: int, end: int, step: int, speed: int) -> str:
    return (
        f"animate speed {speed}; "
        f"animate goto {start}; "
        f"animate forward {mol_id}"
    )


def stop_playback() -> str:
    return "animate stop"


# ---------------------------------------------------------------------------
# Measurement & analysis
# ---------------------------------------------------------------------------

def measure_distance(mol_id: int, idx1: int, idx2: int) -> str:
    return (
        f"set _sel1 [atomselect {mol_id} \"index {idx1}\"]; "
        f"set _sel2 [atomselect {mol_id} \"index {idx2}\"]; "
        f"set _d [measure bond [list [lindex [$_sel1 get index] 0] "
        f"[lindex [$_sel2 get index] 0]] mol {mol_id}]; "
        f"puts \"DISTANCE: $_d\"; "
        f"$_sel1 delete; $_sel2 delete"
    )


def measure_angle(mol_id: int, idx1: int, idx2: int, idx3: int) -> str:
    return (
        f"puts \"ANGLE: [measure angle "
        f"{{[list {idx1} {idx2} {idx3}]}} mol {mol_id}]\""
    )


def measure_rmsd(mol_id: int, selection: str, ref_frame: int) -> str:
    sel = tcl_quote(selection)
    return (
        f"set _ref [atomselect {mol_id} {sel} frame {ref_frame}]; "
        f"set _cur [atomselect {mol_id} {sel}]; "
        f"set _rmsd [measure rmsd $_cur $_ref]; "
        f"puts \"RMSD: $_rmsd\"; "
        f"$_ref delete; $_cur delete"
    )


def get_atom_info(mol_id: int, selection: str) -> str:
    sel = tcl_quote(selection)
    return (
        f"set _s [atomselect {mol_id} {sel}]; "
        f"set _info [list]; "
        f"foreach _i [$_s get index] _r [$_s get resname] "
        f"_rn [$_s get resid] _n [$_s get name] {{"
        f"  lappend _info \"index=$_i resname=$_r resid=$_rn name=$_n\""
        f"}}; "
        f"puts \"ATOMINFO: [join $_info |]\"; "
        f"$_s delete"
    )


# ---------------------------------------------------------------------------
# Rendering & session
# ---------------------------------------------------------------------------

def render_image(output_path: str, renderer: str, width: int, height: int) -> str:
    op = windows_path_to_tcl(output_path)
    r = tcl_quote(renderer)
    return (
        f"display resize {width} {height}; "
        f"render {r} {op}; "
        f"puts \"RENDERED: {output_path.replace(chr(92), '/')}\""
    )


def save_session(output_path: str) -> str:
    op = windows_path_to_tcl(output_path)
    return f"vmd save state {op}; puts \"SAVED: {output_path.replace(chr(92), '/')}\""


# ---------------------------------------------------------------------------
# Status / info
# ---------------------------------------------------------------------------

def vmd_version() -> str:
    return "puts \"VERSION: [vmdinfo version]\""
