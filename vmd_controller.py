from __future__ import annotations
import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = {
    "vmd_executable": r"C:\Program Files\University of Illinois\VMD2\vmd.exe",
    "communication_mode": "subprocess",
    "subprocess_timeout": 10.0,
    "allowed_directories": [],
    "log_level": "INFO",
}

_CONFIG_PATH = Path(__file__).parent / "vmd_mcp_config.json"

# ---------------------------------------------------------------------------
# How the subprocess protocol works
# ---------------------------------------------------------------------------
# VMD in text mode runs a C-level interactive loop that:
#   1. Prints "vmd > " prompt (no newline)
#   2. Reads one line from stdin via C gets()
#   3. Evaluates it with Tcl_Eval()
#   4. Goes back to step 1
#
# Our startup Tcl script installs an "after 20" polling callback that fires
# DURING Tcl_Eval() (after the command runs) and reads stdin.
#
# Python sends:   <tcl_command>\n::EXEC::\n
# Result:
#   - Interactive loop reads <tcl_command>, evaluates it → output printed with
#     "vmd > " prefix on the first line (subsequent puts lines have no prefix)
#   - During Tcl_Eval, the "after" callback fires, reads "::EXEC::", prints
#     "VMDDONE" and flushes → Python knows the command is done
#   - Python strips the "vmd > " prefix from the first output line and
#     collects all lines until VMDDONE
# ---------------------------------------------------------------------------

_VMD_STDIN_LOOP = r"""
set _vmd_buf {}

# Define ::EXEC:: as a harmless no-op so VMD's interactive mode doesn't error
# if it happens to read the terminator line instead of our after-callback.
namespace eval ::_VMDMCP_ {}
proc ::_VMDMCP_::exec_marker {} {}

proc _vmd_check_stdin {} {
    global _vmd_buf
    if {[eof stdin]} { return }
    if {[gets stdin _line] >= 0} {
        if {$_line eq {::EXEC::}} {
            set _r {}
            catch {uplevel #0 $_vmd_buf} _r
            set _vmd_buf {}
            if {[string length $_r] > 0} { puts $_r }
            puts VMDDONE
            flush stdout
        } else {
            append _vmd_buf $_line\n
        }
    }
    after 20 _vmd_check_stdin
}

vmdcon -info VMD_STARTUP_COMPLETE
flush stdout
after 0 _vmd_check_stdin
set _vmd_buf {}
"""

_VMD_PROMPT = "vmd > "


def load_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception as exc:
            logger.warning("Could not read config: %s", exc)
    return cfg


def validate_path(file_path: str, config: dict) -> Path:
    p = Path(file_path).resolve()
    allowed = config.get("allowed_directories", [])
    if allowed:
        if not any(
            str(p).lower().startswith(str(Path(d).resolve()).lower())
            for d in allowed
        ):
            raise ValueError(f"Path '{p}' is outside allowed directories.")
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p


class VMDSubprocessController:
    """Controls VMD via a persistent subprocess.

    Uses VMD's C-level interactive stdin loop for command execution, with a
    Tcl "after 20" callback that fires during each Tcl_Eval to emit the
    VMDDONE sentinel after each command completes.
    """

    SENTINEL = "VMDDONE"
    EXEC_MARKER = "::EXEC::"
    STARTUP_SIGNAL = "VMD_STARTUP_COMPLETE"

    def __init__(self, executable: str, timeout: float = 10.0, subprocess_args: Optional[list] = None):
        self.executable = executable
        self.timeout = timeout
        self.subprocess_args = subprocess_args or ["-dispdev", "text"]
        self._proc: Optional[subprocess.Popen] = None
        self._stdout_queue: queue.Queue = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._startup_tcl: Optional[str] = None

    # Compatibility shims for vmd_mcp_server.py status resource
    @property
    def host(self) -> str:
        return "subprocess"

    @property
    def port(self) -> int:
        return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def connect(self) -> None:
        if self.connected:
            return

        fd, self._startup_tcl = tempfile.mkstemp(suffix=".tcl", prefix="vmd_loop_")
        os.write(fd, _VMD_STDIN_LOOP.encode("utf-8"))
        os.close(fd)

        kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._proc = subprocess.Popen(
            [self.executable, *self.subprocess_args, "-e", self._startup_tcl],
            **kwargs,
        )
        self._stdout_queue = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._read_stdout_loop, daemon=True
        )
        self._reader_thread.start()

        self._wait_for_startup()
        logger.info("VMD subprocess ready (pid=%d)", self._proc.pid)

    def disconnect(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.write(b"exit\n")
                self._proc.stdin.flush()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

        if self._startup_tcl and os.path.exists(self._startup_tcl):
            try:
                os.unlink(self._startup_tcl)
            except OSError:
                pass
            self._startup_tcl = None

    def ensure_connected(self) -> None:
        if not self.connected:
            self.connect()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def send_command(self, tcl_cmd: str) -> str:
        if not self.connected:
            raise ConnectionError("VMD subprocess is not running.")

        # Interactive mode reads tcl_cmd; the after callback reads ::EXEC:: and
        # emits VMDDONE.  Both lines must be written atomically in one flush.
        payload = f"{tcl_cmd.strip()}\n{self.EXEC_MARKER}\n"
        logger.debug("→ VMD: %s", payload[:200])
        try:
            self._proc.stdin.write(payload.encode("utf-8"))
            self._proc.stdin.flush()
        except OSError as exc:
            self._proc = None
            raise ConnectionError(f"Failed to write to VMD stdin: {exc}") from exc

        lines: list[str] = []
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"VMD did not respond within {self.timeout}s.")
            try:
                line = self._stdout_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue

            if line is None:
                self._proc = None
                raise ConnectionError("VMD process ended unexpectedly.")

            stripped = line.strip()

            # Strip the interactive-mode prompt prefix (first output line only)
            if stripped.startswith(_VMD_PROMPT):
                stripped = stripped[len(_VMD_PROMPT):].strip()

            if stripped == self.SENTINEL:
                break

            # Filter noise: after-callback IDs, pure prompts, empty lines
            if not stripped or stripped.startswith("after#"):
                continue

            lines.append(stripped)

        result = "\n".join(lines).strip()
        logger.debug("← VMD: %s", result[:200])
        return result

    def send_safe(self, tcl_cmd: str) -> str:
        self.ensure_connected()
        return self.send_command(tcl_cmd)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_stdout_loop(self) -> None:
        try:
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    break
                self._stdout_queue.put(
                    line.decode("utf-8", errors="replace").rstrip("\r\n")
                )
        finally:
            self._stdout_queue.put(None)

    def _wait_for_startup(self) -> None:
        """Drain banner output; return once the startup-complete signal arrives."""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("VMD did not become ready within timeout.")
            try:
                line = self._stdout_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if line is None:
                raise ConnectionError("VMD process exited during startup.")
            if self.STARTUP_SIGNAL in line:
                return


# Backward-compat alias — vmd_mcp_server.py imports this name
VMDSocketController = VMDSubprocessController


def make_controller() -> VMDSubprocessController:
    cfg = load_config()
    timeout = float(
        cfg.get("subprocess_timeout", cfg.get("socket_timeout", 10.0))
    )
    return VMDSubprocessController(
        executable=cfg.get("vmd_executable", _DEFAULT_CONFIG["vmd_executable"]),
        timeout=timeout,
        subprocess_args=cfg.get("subprocess_args"),
    )


# ---------------------------------------------------------------------------
# Smoke test: python vmd_controller.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    ctrl = make_controller()
    print("Connecting to VMD…")
    ctrl.connect()
    print("Connected.")
    result = ctrl.send_command("puts hello")
    print(f"Response: {result!r}")
    assert result == "hello", f"Expected 'hello', got {result!r}"
    print("Smoke test PASSED.")
    ctrl.disconnect()
