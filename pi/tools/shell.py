import os
import pty
import select
import sys
import signal
import threading
import shlex
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

_SHELL_PID = None
_SHELL_FD = None
_COUNTER = 0
_LOCK = threading.Lock()

# Captured interactive session state
_INTERACTIVE_MODE = False
_ACTIVE_MARKER = None

# Capture cwd from the importing process
_START_CWD = os.getcwd()
_DOTENV_LOADED = False
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

MAX_SHELL_OUTPUT_CHARS = int(os.environ.get("MAX_SHELL_OUTPUT", "200000"))
DEFAULT_TAIL_LINES = int(os.environ.get("SHELL_TAIL_LINES", "50"))
TRUNCATE_MIN_LINES = int(os.environ.get("SHELL_TRUNCATE_MIN_LINES", "20"))
INTERACTIVE_IDLE_SECONDS = float(os.environ.get("SHELL_INTERACTIVE_IDLE_SECONDS", "30"))
INTERACTIVE_PROMPT_GRACE_SECONDS = float(os.environ.get("SHELL_INTERACTIVE_PROMPT_GRACE_SECONDS", "0.5"))

TRUNCATE_EXEMPT_COMMANDS = {
    "cat",
    "echo",
    "printf",
    "head",
    "tail",
    "sed",
    "awk",
    "grep",
    "less",
    "more",
    "cut",
    "tr",
    "pv",
    "watch",
}

# Per-command truncation/filtering policies.
DEFAULT_TRUNCATION_POLICIES = {
    re.compile(r"\bnmap\b"): {
        "suppress_patterns": [
            r"^\s*Starting Nmap.*$",
            r"^\s*Initiating.*$",
            r"^\s*Completed.*$",
            r"\d+%|[\r\x08]",
            r"^\s*Timing:.*$",
            r"^\s*RTT.*$",
        ],
        "tail_lines": 50,
    },
    re.compile(r"\bapt\b|\bapt-get\b"): {
        "suppress_patterns": [
            r"^\s*Get:\s*",
            r"^\s*Downloading.*$",
            r"^\s*Fetched.*$",
            r"^\s*Reading package lists.*$",
            r"^\s*Building dependency tree.*$",
            r"^\s*Reading state information.*$",
            r"^\s*\d+%.*$",
            r"^\s*Preparing to unpack.*$",
            r"^\s*Unpacking.*$",
            r"^\s*Setting up.*$",
            r"^\s*Processing triggers for.*$",
        ],
        "tail_lines": 50,
    },
}

# Heuristics for interactive waits/prompt states.
INTERACTIVE_PROMPT_PATTERNS = [
    r"(?i)\[sudo\]\s*password\s*for\s*.*:\s*$",
    r"(?i)(?:^|\n)\s*password\s*:\s*$",
    r"(?i)(?:^|\n)\s*passphrase\s*:\s*$",
    r"(?i)(?:^|\n).*(?:continue connecting|are you sure you want to continue connecting).*(?:yes/no|y/n).*$",
    r"(?i)(?:^|\n).*\((?:yes/no|y/n)\)\s*\??\s*$",
    r"(?m)^(?:mysql|mariadb|psql|sqlite3|ftp|ssh|plink|python|python3|ipython|bash|sh|zsh|powershell|cmd|node|ruby|perl|gdb|lldb|nc|telnet)[^\n]*[>#] ?$",
    r"(?m)^.*@\S+:[^#\n]*[#\$]\s*$",
    r"(?m)^.*:>\s*$",
    r"(?m)^.*»\s*$",
    r"(?m)^\s*>>> ?$",
    r"(?m)^\s*\.\.\. ?$",
    r"(?i)(?:^|\n).*press\s+(?:enter|return|any key).*$",
    r"(?i)(?:^|\n).*type\s+help\s+for\s+help.*$",
    r"(?i)(?:^|\n).*hit\s+enter.*$",
]


def _shell_alive() -> bool:
    global _SHELL_PID

    if _SHELL_PID is None:
        return False

    try:
        os.kill(_SHELL_PID, 0)
        return True
    except OSError:
        return False


def _drain(timeout: float = 0.05) -> str:
    global _SHELL_FD

    if _SHELL_FD is None:
        return ""

    chunks = []

    while True:
        r, _, _ = select.select([_SHELL_FD], [], [], timeout)

        if not r:
            break

        try:
            chunk = os.read(_SHELL_FD, 4096).decode("utf-8", errors="replace")
        except OSError:
            break

        chunks.append(chunk)

    return "".join(chunks)


def _start_shell() -> None:
    global _SHELL_PID, _SHELL_FD

    if _shell_alive():
        return

    pid, fd = pty.fork()

    if pid == 0:
        # CHILD PROCESS
        os.chdir(_START_CWD)

        os.environ["TERM"] = "xterm-256color"
        os.environ["LANG"] = "C.UTF-8"
        os.environ["LC_ALL"] = "C.UTF-8"

        os.execvp(
            "/bin/bash",
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-i",
            ],
        )

    # PARENT PROCESS
    _SHELL_PID = pid
    _SHELL_FD = fd

    # Disable prompt
    os.write(_SHELL_FD, b"export PS1=''\n")

    # Disable command echoing
    os.write(_SHELL_FD, b"stty -echo\n")

    # Clear startup noise
    _drain()


def _escape_single_quotes(s: str) -> str:
    """Escape single quotes for safe inclusion in single-quoted shell strings."""
    return s.replace("'", "'\"'\"'")


def _strip_ansi(s: str) -> str:
    """Remove common ANSI escape sequences from terminal output."""
    try:
        return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", s)
    except re.error:
        return s


def _sanitize_output(s: str, sudo_password: Optional[str], sudo_prompt: Optional[str]) -> str:
    """Sanitize PTY/subprocess output to remove prompts, passwords, and control sequences."""
    if not s:
        return ""

    s = _strip_ansi(s)

    # Remove our custom marker if it leaks through
    s = re.sub(r"__CMD_DONE_\d+__", "", s)

    if sudo_password:
        try:
            s = re.sub(
                rf"(?m)^\s*bash:\s*{re.escape(sudo_password)}:\s*command not found\s*$",
                "",
                s,
            )
        except re.error:
            pass
        s = s.replace(sudo_password, "")

    s = re.sub(r"(?mi)^\s*\[sudo\]\s*password\s*for\s*.*:.*$", "", s)
    s = re.sub(r"(?mi)^\s*sudo:.*password.*$", "", s)
    s = re.sub(r"(?m)^\s*sudo: a password is required\s*$", "", s)

    s = re.sub(r"(?m)^printf .*\\n$", "", s)
    s = re.sub(r"(?m)^\s*bash: .*: command not found\s*$", "", s)

    lines = [ln for ln in s.splitlines() if ln.strip() != ""]
    return "\n".join(lines).strip()


def _load_dotenv(path: str) -> None:
    """Load simple KEY=VALUE lines from a .env file into os.environ if missing."""
    try:
        if not os.path.exists(path):
            return

        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                if "=" not in line:
                    continue

                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()

                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]

                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        return


def _match_truncation_policy(command_str: str):
    if not command_str:
        return None
    for pat, policy in DEFAULT_TRUNCATION_POLICIES.items():
        try:
            if pat.search(command_str):
                return policy
        except Exception:
            continue
    return None


def _is_truncation_exempt(command_str: str) -> bool:
    """Return True if the command is a simple utility that should not be truncated."""
    if not command_str:
        return False
    try:
        tokens = shlex.split(command_str)
    except Exception:
        tokens = command_str.split()
    if not tokens:
        return False

    tok = tokens[0]
    if tok == "sudo" and len(tokens) > 1:
        tok = tokens[1]

    if tok in ("sh", "bash") and "-c" in tokens:
        try:
            cidx = tokens.index("-c")
            cmd_str = tokens[cidx + 1] if cidx + 1 < len(tokens) else ""
            try:
                inner = shlex.split(cmd_str)[0] if cmd_str else ""
            except Exception:
                inner = cmd_str.split()[0] if cmd_str else ""
            tok = inner or tok
        except Exception:
            pass

    return tok in TRUNCATE_EXEMPT_COMMANDS


def _apply_truncation(output: str, policy: Optional[dict], tail_lines: Optional[int], force_tail: bool = False) -> str:
    if not output:
        return output

    original_len = len(output)
    max_chars = MAX_SHELL_OUTPUT_CHARS
    effective_tail = tail_lines if tail_lines is not None else DEFAULT_TAIL_LINES

    lines = output.splitlines()

    if policy:
        suppress = policy.get("suppress_patterns", [])
        compiled = []
        for p in suppress:
            try:
                compiled.append(re.compile(p))
            except re.error:
                try:
                    compiled.append(re.compile(re.escape(p)))
                except re.error:
                    pass

        filtered = []
        for ln in lines:
            if not ln.strip():
                continue
            skip = False
            for cre in compiled:
                try:
                    if cre.search(ln):
                        skip = True
                        break
                except Exception:
                    continue
            if not skip:
                filtered.append(ln)
    else:
        filtered = [ln for ln in lines if ln.strip() != ""]

    if not filtered or (not policy and force_tail):
        nonempty = [ln for ln in lines if ln.strip() != ""]
        keep = nonempty[-effective_tail:] if effective_tail and len(nonempty) > effective_tail else nonempty
        res = "\n".join(keep).strip()
    else:
        res = "\n".join(filtered).strip()
        if len(res) > max_chars:
            nonempty = [ln for ln in res.splitlines() if ln.strip() != ""]
            keep = nonempty[-effective_tail:] if effective_tail and len(nonempty) > effective_tail else nonempty
            res = "\n".join(keep).strip()

    if original_len > len(res):
        omitted = original_len - len(res)
        res = res + f"\n[...OUTPUT TRUNCATED: {omitted} chars omitted...]\n"

    return res


def _looks_like_interactive_prompt(text: str) -> bool:
    if not text:
        return False

    cleaned = _strip_ansi(text)
    for pat in INTERACTIVE_PROMPT_PATTERNS:
        try:
            if re.search(pat, cleaned):
                return True
        except re.error:
            continue

    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return False

    last = lines[-1]
    if len(last) <= 140 and (last.endswith(":") or last.endswith(">") or last.endswith("?")):
        if "<" not in last and "http" not in last:
            return True

    return False


def _extract_session_label(text: str) -> Optional[str]:
    """Return a stable label for interactive sessions such as @Pigion or @bettercap."""
    if not text:
        return None

    cleaned = _strip_ansi(text)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return None

    for ln in reversed(lines[-10:]):
        m = re.search(r"(?P<user>[^@\s]+)@(?P<host>[^:\s]+)[:#\$].*", ln)
        if m:
            host = m.group("host").strip()
            if host:
                return f"@{host}"

        if "»" in ln:
            return "@bettercap"

        if re.search(r"(?m)^.*:>\s*$", ln):
            return "@interactive"

        if re.search(r"(?m)^.*>\s*$", ln) and not ln.startswith(("http://", "https://")):
            return "@interactive"

    return None


def _extract_path_candidate(text: str) -> Optional[str]:
    """Pull the first obvious path-like line from command output."""
    if not text:
        return None

    cleaned = _strip_ansi(text)
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if re.match(r"^(?:/[^\s]*|~(?:/.*)?|[A-Za-z]:[\\/].*)$", line):
            return line

    return None


def _format_cwd_display(cwd: str, session_label: Optional[str] = None) -> str:
    """Format cwd for state so remote/interactive sessions remain explicit."""
    if not cwd:
        return cwd

    if session_label:
        prefix = f"{session_label} - "
        if not cwd.startswith(prefix):
            return f"{prefix}{cwd}"

    return cwd


def _send_to_shell(text: str) -> None:
    global _SHELL_FD

    if _SHELL_FD is None:
        raise RuntimeError("Shell is not initialized.")

    if text is None:
        text = ""

    if text == "":
        os.write(_SHELL_FD, b"\n")
        return

    for line in text.splitlines():
        os.write(_SHELL_FD, (line + "\n").encode("utf-8"))


def _read_until_marker_or_idle(
    marker: str,
    timeout: float = 0.2,
    stream: bool = True,
    truncate: bool = False,
    truncate_policy: Optional[dict] = None,
    tail_lines: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Read from PTY until marker is seen, or until the shell appears to wait for input
    for INTERACTIVE_IDLE_SECONDS without marker progress.
    """
    global _SHELL_FD

    if _SHELL_FD is None:
        return {"output": "", "completed": False, "interactive_mode": False}

    chunks: List[str] = []
    buffer = ""
    last_activity = time.monotonic()
    prompt_seen = False

    while True:
        r, _, _ = select.select([_SHELL_FD], [], [], timeout)

        if not r:
            idle_for = time.monotonic() - last_activity

            if prompt_seen and _shell_alive() and idle_for >= INTERACTIVE_PROMPT_GRACE_SECONDS:
                raw = "".join(chunks)
                if marker in raw:
                    raw = raw.split(marker)[0]

                policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(raw)
                nonempty = [ln for ln in raw.splitlines() if ln.strip() != ""]
                should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(raw))
                if should_trunc:
                    raw = _apply_truncation(raw, policy, tail_lines, force_tail=truncate)

                return {
                    "output": _sanitize_output(raw, None, None),
                    "completed": False,
                    "interactive_mode": True,
                }

            # If no marker after a long idle period, assume the process is waiting
            # for user input.
            if _shell_alive() and idle_for >= INTERACTIVE_IDLE_SECONDS:
                raw = "".join(chunks)
                if marker in raw:
                    raw = raw.split(marker)[0]

                policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(raw)
                nonempty = [ln for ln in raw.splitlines() if ln.strip() != ""]
                should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(raw))
                if should_trunc:
                    raw = _apply_truncation(raw, policy, tail_lines, force_tail=truncate)

                return {
                    "output": _sanitize_output(raw, None, None),
                    "completed": False,
                    "interactive_mode": True,
                }

            continue

        try:
            chunk = os.read(_SHELL_FD, 4096).decode("utf-8", errors="replace")
        except OSError:
            break

        if not chunk:
            continue

        chunks.append(chunk)
        buffer += chunk
        last_activity = time.monotonic()

        if _looks_like_interactive_prompt(buffer):
            prompt_seen = True

        if stream:
            try:
                if marker in chunk:
                    to_print = chunk.split(marker)[0]
                else:
                    to_print = chunk
                sys.stdout.write(to_print)
                sys.stdout.flush()
            except Exception:
                pass

        if marker in buffer:
            raw = "".join(chunks)
            raw = raw.split(marker)[0]

            policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(raw)
            nonempty = [ln for ln in raw.splitlines() if ln.strip() != ""]
            should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(raw))
            if should_trunc:
                raw = _apply_truncation(raw, policy, tail_lines, force_tail=truncate)

            if prompt_seen or _looks_like_interactive_prompt(raw):
                return {
                    "output": _sanitize_output(raw, None, None),
                    "completed": False,
                    "interactive_mode": True,
                }

            return {
                "output": _sanitize_output(raw, None, None),
                "completed": True,
                "interactive_mode": False,
            }

        if not _shell_alive():
            break

    raw = "".join(chunks)
    if marker in raw:
        raw = raw.split(marker)[0]

    policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(raw)
    nonempty = [ln for ln in raw.splitlines() if ln.strip() != ""]
    should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(raw))
    if should_trunc:
        raw = _apply_truncation(raw, policy, tail_lines, force_tail=truncate)

    return {
        "output": _sanitize_output(raw, None, None),
        "completed": False,
        "interactive_mode": prompt_seen,
    }


def run_shell(
    command,
    sudo: bool = False,
    sudo_password: Optional[str] = None,
    timeout: float = 0.2,
    stream: bool = True,
    truncate: bool = False,
    truncate_policy: Optional[dict] = None,
    tail_lines: Optional[int] = None,
):
    """
    Execute a shell command in a persistent pty-backed bash.

    If output stalls for INTERACTIVE_IDLE_SECONDS and marker is not seen, return:
        {
            "output": "...partial output...",
            "interactive_mode": True,
            "completed": False
        }

    If _INTERACTIVE_MODE is already active, treat `command` as INPUT to the
    currently running process and do not create a new marker.
    """
    global _COUNTER, _INTERACTIVE_MODE, _ACTIVE_MARKER

    with _LOCK:
        _start_shell()

        # If we are already in interactive mode, treat the incoming text as input
        # to the live process, not a fresh command.
        continuing_interactive = _INTERACTIVE_MODE and (_ACTIVE_MARKER is not None)

        # Keep old sudo handling behavior.
        if sudo:
            if sudo_password is None:
                sudo_password = os.environ.get("SUDO_PASSWORD") or os.environ.get("TEST_SUDO_PASSWORD")

                if (sudo_password is None) and (not _DOTENV_LOADED):
                    _load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
                    globals()["_DOTENV_LOADED"] = True
                    sudo_password = os.environ.get("SUDO_PASSWORD") or os.environ.get("TEST_SUDO_PASSWORD")

            tokens = []
            try:
                tokens = shlex.split(command)
            except Exception:
                tokens = command.split()

            options = []
            rest_tokens = []

            if tokens and tokens[0] == "sudo":
                i = 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    options.append(tokens[i])
                    i += 1
                rest_tokens = tokens[i:]
            else:
                rest_tokens = tokens if tokens else [command]

            rest_command = " ".join(rest_tokens).strip()

            # If we have a sudo password, keep existing non-interactive behavior.
            if sudo_password is not None:
                try:
                    first_tok = tokens[0]
                except Exception:
                    first_tok = None

                if first_tok == "sudo":
                    if len(tokens) > 1:
                        rest_join = shlex.join(tokens[1:])
                        full_cmd = f"sudo -S -p '' {rest_join}"
                    else:
                        full_cmd = f"sudo -S -p '' {rest_command}"
                else:
                    full_cmd = f"sudo -S -p '' {rest_command}"

                proc = subprocess.Popen(
                    full_cmd,
                    shell=True,
                    cwd=_START_CWD,
                    env=os.environ.copy(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    executable="/bin/bash",
                    bufsize=1,
                    universal_newlines=True,
                )

                try:
                    if proc.stdin and sudo_password is not None:
                        proc.stdin.write(sudo_password + "\n")
                        proc.stdin.flush()
                except Exception:
                    pass

                out_chunks = []
                if proc.stdout is not None:
                    for line in proc.stdout:
                        out_chunks.append(line)
                        if stream:
                            try:
                                sys.stdout.write(line)
                                sys.stdout.flush()
                            except Exception:
                                pass

                proc.wait()
                raw_out = "".join(out_chunks)
                policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(command)
                nonempty = [ln for ln in raw_out.splitlines() if ln.strip() != ""]
                should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(command))
                if should_trunc:
                    processed = _apply_truncation(raw_out, policy, tail_lines, force_tail=truncate)
                else:
                    processed = raw_out

                return {
                    "output": _sanitize_output(processed, sudo_password, None),
                    "interactive_mode": False,
                    "completed": True,
                }

            # If no sudo password is available, fall through to PTY path.
            escaped = _escape_single_quotes(rest_command)
            opt_str = (" " + " ".join(options)) if options else ""
            wrapped = f"sudo -n{opt_str} bash -c '{escaped}'"
            _send_to_shell(wrapped)

        else:
            if continuing_interactive:
                # Feed INPUT to the existing live process.
                SPECIAL_KEYS = {
    "SIGINT": b"\x03",  # SIGINT
    "EOF": b"\x04",  # EOF
    "SIGTSTP": b"\x1A",  # SIGTSTP
}           
                if command in SPECIAL_KEYS:
                    os.write(_SHELL_FD, SPECIAL_KEYS[command])
                else:
                    _send_to_shell(command)

            else:
                _COUNTER += 1
                marker = f"__CMD_DONE_{_COUNTER}__"
                _ACTIVE_MARKER = marker

                # Send command
                for line in command.splitlines():
                    os.write(_SHELL_FD, (line + "\n").encode("utf-8"))

                # Completion marker. If the command enters an interactive program,
                # this marker will only be reached once that program exits.
                os.write(_SHELL_FD, f'printf "{marker}\\n"\n'.encode("utf-8"))

        marker = _ACTIVE_MARKER if _ACTIVE_MARKER is not None else f"__CMD_DONE_{_COUNTER}__"

        read_result = _read_until_marker_or_idle(
            marker=marker,
            timeout=timeout,
            stream=stream,
            truncate=truncate,
            truncate_policy=truncate_policy,
            tail_lines=tail_lines,
        )

        output = read_result["output"]
        completed = bool(read_result["completed"])
        interactive_mode = bool(read_result["interactive_mode"])

        if completed:
            _INTERACTIVE_MODE = False
            _ACTIVE_MARKER = None
        elif interactive_mode:
            _INTERACTIVE_MODE = True
            # Keep marker alive so later INPUT can continue the same session.
        else:
            if continuing_interactive:
                _INTERACTIVE_MODE = True

        return {
            "output": output,
            "interactive_mode": _INTERACTIVE_MODE,
            "completed": completed,
        }


def shell_reset(command=None, memory=None, local_state = None):
    global _SHELL_PID, _SHELL_FD, _INTERACTIVE_MODE, _ACTIVE_MARKER

    pid = _SHELL_PID
    fd = _SHELL_FD

    _SHELL_PID = None
    _SHELL_FD = None
    _INTERACTIVE_MODE = False
    _ACTIVE_MARKER = None

    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

        try:
            os.waitpid(pid, 0)
        except OSError:
            pass

    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def shell(command, memory, local_state):
    result = run_shell(command, stream=True, truncate=True)

    output = str(result.get("output", ""))
    interactive_mode = bool(result.get("interactive_mode", False))
    completed = bool(result.get("completed", True))

    # Track both input and output in state.
    local_state["last_action"] = command
    local_state["last_tool_output"] = output
    local_state["INTERACTIVE_MODE"] = "ON" if interactive_mode else "OFF"
    local_state["INTERACTIVE_COMPLETED"] = completed

    # Preserve a stable label for interactive sessions such as @Pigion or @bettercap.
    session_label = _extract_session_label(output) or local_state.get("SESSION_LABEL")
    if session_label:
        local_state["SESSION_LABEL"] = session_label

    # Update cwd from either explicit cwd output or from remote-shell context.
    try:
        cwd_candidate = _extract_path_candidate(output)

        if cwd_candidate:
            local_state["CURRENT_WORKING_DIRECTORY"] = _format_cwd_display(cwd_candidate, session_label)
        elif not interactive_mode and completed:
            pwd_result = run_shell("pwd", stream=False, truncate=False)
            if isinstance(pwd_result, dict):
                pwd_out = str(pwd_result.get("output", ""))
            else:
                pwd_out = str(pwd_result)

            pwd_candidate = _extract_path_candidate(pwd_out)
            if pwd_candidate:
                local_state["CURRENT_WORKING_DIRECTORY"] = _format_cwd_display(pwd_candidate, session_label)
        elif interactive_mode and session_label and local_state.get("CURRENT_WORKING_DIRECTORY"):
            existing = str(local_state.get("CURRENT_WORKING_DIRECTORY", "")).strip()
            if existing and not existing.startswith(f"{session_label} - "):
                if re.match(r"^(?:/[^\s]*|~(?:/.*)?|[A-Za-z]:[\\/].*)$", existing):
                    local_state["CURRENT_WORKING_DIRECTORY"] = _format_cwd_display(existing, session_label)
    except Exception:
        local_state["CURRENT_WORKING_DIRECTORY"] = local_state.get("CURRENT_WORKING_DIRECTORY", os.getcwd())

    return {
        "ok": True,
        "output": output,
        "memory": memory,
        "state": local_state,
        "interactive_mode": interactive_mode,
        "completed": completed,
    }
