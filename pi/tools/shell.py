import os
import pty
import select
import sys
import signal
import threading
import shlex
import re
import subprocess

_SHELL_PID = None
_SHELL_FD = None
_COUNTER = 0
_LOCK = threading.Lock()

# Capture cwd from the importing process
_START_CWD = os.getcwd()
_DOTENV_LOADED = False
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAX_SHELL_OUTPUT_CHARS = int(os.environ.get("MAX_SHELL_OUTPUT", "200000"))
DEFAULT_TAIL_LINES = int(os.environ.get("SHELL_TAIL_LINES", "50"))
TRUNCATE_MIN_LINES = int(os.environ.get("SHELL_TRUNCATE_MIN_LINES", "20"))
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

# Per-command truncation/filtering policies. Keys are compiled regexes
# that match the command string. Policies contain `suppress_patterns` (list
# of regex strings to remove) and an optional `tail_lines` fallback.
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


def _shell_alive():
    global _SHELL_PID

    if _SHELL_PID is None:
        return False

    try:
        os.kill(_SHELL_PID, 0)
        return True
    except OSError:
        return False


def _drain(timeout=0.05):
    global _SHELL_FD

    chunks = []

    while True:
        r, _, _ = select.select([_SHELL_FD], [], [], timeout)

        if not r:
            break

        try:
            chunk = os.read(_SHELL_FD, 4096).decode(
                "utf-8",
                errors="replace",
            )
        except OSError:
            break

        chunks.append(chunk)

    return "".join(chunks)


def _start_shell():
    global _SHELL_PID, _SHELL_FD

    if _shell_alive():
        return

    pid, fd = pty.fork()

    if pid == 0:
        # CHILD PROCESS

        # Start in caller's cwd
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
    os.write(
        _SHELL_FD,
        b"export PS1=''\n",
    )

    # Disable command echoing
    os.write(
        _SHELL_FD,
        b"stty -echo\n",
    )

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


def _sanitize_output(s: str, sudo_password: str | None, sudo_prompt: str | None) -> str:
    """Sanitize PTY/subprocess output to remove prompts, passwords, and control sequences.

    This is best-effort: it strips ANSI escapes, known sudo prompts, and
    attempts to remove occurrences of the password and common bash/sudo noise.
    """
    if not s:
        return ""

    # Strip ANSI sequences first
    s = _strip_ansi(s)

    # Remove our custom sudo prompt markers
    s = re.sub(r"__SUDO_PROMPT_\d+__", "", s)

    # Remove literal password echoes (best-effort)
    if sudo_password:
        try:
            # remove full-line bash errors that echo the password as a command
            s = re.sub(rf"(?m)^\s*bash:\s*{re.escape(sudo_password)}:\s*command not found\s*$", "", s)
        except re.error:
            pass
        s = s.replace(sudo_password, "")

    # Remove sudo password prompt lines
    s = re.sub(r"(?mi)^\s*\[sudo\]\s*password\s*for\s*.*:.*$", "", s)

    # Remove common sudo/binary messages that are not command output
    s = re.sub(r"(?mi)^\s*sudo:.*password.*$", "", s)
    s = re.sub(r"(?m)^\s*sudo: a password is required\s*$", "", s)

    # Remove printf/pipe artifacts
    s = re.sub(r"(?m)^printf .*\\n$", "", s)

    # Remove remaining bash 'command not found' lines
    s = re.sub(r"(?m)^\s*bash: .*: command not found\s*$", "", s)

    # Collapse blank lines introduced by removals
    lines = [ln for ln in s.splitlines() if ln.strip() != ""]
    return "\n".join(lines).strip()


def _load_dotenv(path: str) -> None:
    """Load simple KEY=VALUE lines from a .env file into os.environ if missing.

    This is intentionally minimal: it ignores export keywords and comments,
    and only sets variables that are not already present in the environment.
    """
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
                # remove surrounding quotes
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]

                if key and (key not in os.environ):
                    os.environ[key] = val
    except Exception:
        # Fail silently — dotenv is a convenience only
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
    # skip leading sudo
    tok = tokens[0]
    if tok == "sudo" and len(tokens) > 1:
        tok = tokens[1]
    # If the command is a shell -c wrapper, try to inspect the executed token.
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


def _apply_truncation(output: str, policy: dict | None, tail_lines: int | None, force_tail: bool = False):
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

    # If caller asked to force tailing, or suppression removed everything,
    # return the last `effective_tail` non-empty lines.
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


def shell(command, sudo: bool = False, sudo_password: str | None = None, timeout: float = 1.0, stream: bool = True, truncate: bool = False, truncate_policy: dict | None = None, tail_lines: int | None = None):
    """Execute a shell `command` in a persistent pty-backed bash.

    If `sudo` is True and `sudo_password` is provided, the function will
    run the command via `sudo -S -p <PROMPT>` and send the password when
    sudo prompts. If `sudo` is True and `sudo_password` is None, the
    command is executed with `sudo -n` (non-interactive) so it fails fast
    if a password is required.
    """
    global _COUNTER

    with _LOCK:
        _start_shell()

        _COUNTER += 1
        marker = f"__CMD_DONE_{_COUNTER}__"

        password_sent = False
        sudo_prompt = None
        piped_pw = False

        # Prepare and send the command
        if sudo:
            # If no explicit password provided, try environment and .env
            if sudo_password is None:
                # Try existing environment first
                sudo_password = os.environ.get("SUDO_PASSWORD") or os.environ.get("TEST_SUDO_PASSWORD")

                # Load .env lazily if not found
                if (sudo_password is None) and (not _DOTENV_LOADED):
                    # .env is located in the project root
                    _load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
                    # mark loaded regardless of success to avoid repeated I/O
                    globals()["_DOTENV_LOADED"] = True
                    sudo_password = os.environ.get("SUDO_PASSWORD") or os.environ.get("TEST_SUDO_PASSWORD")

            # Parse the command to handle cases where user already prefixed with sudo
            tokens = []
            try:
                tokens = shlex.split(command)
            except Exception:
                tokens = command.split()

            options = []
            rest_tokens = []

            if tokens and tokens[0] == "sudo":
                # collect sudo options (tokens starting with '-')
                i = 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    options.append(tokens[i])
                    i += 1

                rest_tokens = tokens[i:]
            else:
                rest_tokens = tokens if tokens else [command]

            rest_command = " ".join(rest_tokens).strip()
            # If we have a sudo password, run the sudo command in a subprocess
            # and return its output directly (avoids PTY prompt noise).
            if sudo_password is not None:
                # Build the command string to execute under /bin/bash -c
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

                # Use Popen so we can stream stdout while supplying the
                # password on stdin immediately. This allows callers to see
                # incremental output for long-running commands.
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

                # Send the password early so sudo can consume it when needed.
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
                return _sanitize_output(processed, sudo_password, None)

            # Non-interactive sudo: don't prompt for password (use PTY)
            escaped = _escape_single_quotes(rest_command)
            opt_str = (" " + " ".join(options)) if options else ""
            wrapped = f"sudo -n{opt_str} bash -c '{escaped}'"

            os.write(_SHELL_FD, (wrapped + "\n").encode("utf-8"))
        else:
            # Send command line-by-line (existing behaviour)
            for line in command.splitlines():
                os.write(_SHELL_FD, (line + "\n").encode("utf-8"))

        # Send completion marker
        os.write(
            _SHELL_FD,
            f'printf "{marker}\\n"\n'.encode("utf-8"),
        )

        output = []
        buffer = ""

        while True:
            r, _, _ = select.select([_SHELL_FD], [], [], timeout)

            if not r:
                continue

            try:
                chunk = os.read(_SHELL_FD, 4096).decode(
                    "utf-8",
                    errors="replace",
                )
            except OSError:
                break

            output.append(chunk)
            buffer += chunk

            # Stream to local stdout immediately if requested
            if stream and chunk:
                try:
                    if marker in chunk:
                        to_print = chunk.split(marker)[0]
                    else:
                        to_print = chunk
                    # Print raw chunk so interactive programs (like nmap)
                    # retain their formatting/animation.
                    sys.stdout.write(to_print)
                    sys.stdout.flush()
                except Exception:
                    pass

            # If we detect the sudo prompt and have a password, send it once
            if sudo_prompt and (sudo_prompt in buffer) and (not password_sent):
                if (not piped_pw) and (sudo_password is not None):
                    os.write(_SHELL_FD, (sudo_password + "\n").encode("utf-8"))
                password_sent = True
                # Clear the buffer so we don't try to re-detect the prompt
                buffer = ""

            if marker in chunk:
                break

        result = "".join(output)

        # Remove marker and everything after it
        result = result.split(marker)[0]

        # Apply truncation/filtering to the returned value only (streaming
        # remains unchanged by default). If the caller supplied a
        # `truncate_policy` or `truncate=True`, apply the matched policy.
        policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(command)
        nonempty = [ln for ln in result.splitlines() if ln.strip() != ""]
        should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(command))
        if should_trunc:
            result = _apply_truncation(result, policy, tail_lines, force_tail=truncate)

        # Sanitize PTY output for control sequences and sudo noise
        return _sanitize_output(result, sudo_password, sudo_prompt)


def shell_reset():
    global _SHELL_PID, _SHELL_FD

    pid = _SHELL_PID
    fd = _SHELL_FD

    _SHELL_PID = None
    _SHELL_FD = None

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