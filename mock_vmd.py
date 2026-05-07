"""
mock_vmd.py — Simulated VMD Tcl server for testing without VMD installed.

Run this in one terminal, then point vmd_mcp_config.json at port 5555.
The mock recognises the key Tcl patterns sent by tcl_builder.py and returns
plausible canned responses so the MCP server can be exercised end-to-end.

Usage::

    python mock_vmd.py             # listens on localhost:5555
    python mock_vmd.py --port 5556 # custom port
"""

from __future__ import annotations

import argparse
import re
import socket
import threading
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MockVMD] %(message)s")
logger = logging.getLogger("mock_vmd")

# ---------------------------------------------------------------------------
# Response logic
# ---------------------------------------------------------------------------

_mol_store: dict[int, dict] = {}   # mol_id → {name, numframes}
_next_mid = 0


def _handle_command(cmd: str) -> str:
    global _next_mid

    lines = []
    for line in cmd.splitlines():
        line = line.strip()
        if not line or line == "puts VMDDONE":
            continue
        lines.append(line)

    full = " ; ".join(lines)

    # --- mol new (load structure) ---
    if "mol new" in full:
        mid = _next_mid
        _next_mid += 1
        # extract filename from braces
        m = re.search(r"mol new \{([^}]+)\}", full)
        name = m.group(1).split("/")[-1] if m else f"mol{mid}"
        _mol_store[mid] = {"name": name, "numframes": 1}
        return f"MOL_ID: {mid}"

    # --- mol addfile (trajectory) ---
    if "mol addfile" in full:
        m = re.search(r"mol addfile \{([^}]+)\}.*mol (\d+)", full)
        if m:
            mid = int(m.group(2))
            if mid in _mol_store:
                _mol_store[mid]["numframes"] = 500  # fake 500 frames
        return ""

    # --- mol delete ---
    m = re.match(r"mol delete (\d+)", full)
    if m:
        mid = int(m.group(1))
        _mol_store.pop(mid, None)
        return ""

    # --- molinfo list (list_molecules) ---
    if "foreach _m [molinfo list]" in full:
        if not _mol_store:
            return ""
        out = []
        for mid, info in _mol_store.items():
            out.append(f"MOL: {mid} {info['name']} {info['numframes']} frames")
        return "\n".join(out)

    # --- molinfo get numframes ---
    m = re.search(r"molinfo (\d+) get numframes", full)
    if m:
        mid = int(m.group(1))
        nf = _mol_store.get(mid, {}).get("numframes", 0)
        return f"NUMFRAMES: {nf}"

    # --- molinfo set frame ---
    m = re.match(r"molinfo (\d+) set frame (\d+)", full)
    if m:
        return ""  # silent success

    # --- mol delrep / mol representation / mol selection / mol color / mol addrep ---
    if "mol representation" in full or "mol delrep" in full or "mol addrep" in full:
        return ""

    # --- color Display Background ---
    if "color Display Background" in full:
        return ""

    # --- display resetview ---
    if "display resetview" in full:
        return ""

    # --- animate ---
    if "animate" in full:
        return ""

    # --- measure bond (distance) ---
    m = re.search(r"measure bond", full)
    if m:
        return "DISTANCE: 3.8245"

    # --- measure angle ---
    if "measure angle" in full:
        return "ANGLE: 109.471"

    # --- measure rmsd ---
    if "measure rmsd" in full:
        return "RMSD: 1.2340"

    # --- atomselect + get ---
    if "atomselect" in full and "get index" in full:
        return "ATOMINFO: index=0 resname=ALA resid=1 name=CA|index=1 resname=ALA resid=1 name=CB"

    # --- render ---
    m = re.search(r"render \{([^}]+)\} \{([^}]+)\}", full)
    if m:
        op = m.group(2)
        return f"RENDERED: {op}"

    # --- vmd save state ---
    if "vmd save state" in full:
        m = re.search(r"vmd save state \{([^}]+)\}", full)
        op = m.group(1) if m else "session.vmd"
        return f"SAVED: {op}"

    # --- vmdinfo version ---
    if "vmdinfo version" in full:
        return "VERSION: 1.9.4a57"

    # --- puts VMDDONE sentinel alone ---
    if full.strip() == "":
        return ""

    # --- unknown: echo back ---
    return f"OK: {full[:60]}"


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr: tuple) -> None:
    logger.info("Client connected from %s", addr)
    buf = ""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")

            # Respond each time we see the sentinel "puts VMDDONE"
            while "puts VMDDONE" in buf:
                idx = buf.index("puts VMDDONE")
                command_block = buf[:idx]
                # Advance past the sentinel line
                after = buf[idx + len("puts VMDDONE"):]
                newline_pos = after.find("\n")
                buf = after[newline_pos + 1:] if newline_pos >= 0 else ""

                response = _handle_command(command_block)
                reply = (response + "\nVMDDONE\n") if response else "VMDDONE\n"
                logger.info("CMD: %s", command_block[:80].replace("\n", " "))
                logger.info("RSP: %s", reply[:80])
                conn.sendall(reply.encode("utf-8"))

    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as exc:
        logger.error("Error handling client: %s", exc)
    finally:
        conn.close()
        logger.info("Client %s disconnected.", addr)


def run_server(host: str = "localhost", port: int = 5555) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    logger.info("Mock VMD Tcl server listening on %s:%d", host, port)
    logger.info("Press Ctrl-C to stop.")
    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        logger.info("Shutting down mock server.")
    finally:
        srv.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock VMD Tcl socket server")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5555)
    args = parser.parse_args()
    run_server(args.host, args.port)
