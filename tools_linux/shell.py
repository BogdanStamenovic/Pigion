import os
import pty
import select
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


def shell(command, sudo: bool = False, sudo_password: str | None = None, timeout: float = 1.0):
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
                # If the user already prefixed with sudo, use the original command
                # otherwise prefix with sudo -S -p ''
                try:
                    first_tok = tokens[0]
                except Exception:
                    first_tok = None

                if first_tok == "sudo":
                    # Reconstruct the rest of the original sudo invocation and
                    # ensure `-S -p ''` is present so sudo reads password from
                    # stdin and does not emit a prompt string.
                    if len(tokens) > 1:
                        rest_join = shlex.join(tokens[1:])
                        full_cmd = f"sudo -S -p '' {rest_join}"
                    else:
                        # fallback to previously-computed rest_command
                        full_cmd = f"sudo -S -p '' {rest_command}"
                else:
                    # note: rest_command already contains the intended command
                    full_cmd = f"sudo -S -p '' {rest_command}"

                # Execute using subprocess to capture exact stdout+stderr
                proc = subprocess.run(
                    full_cmd,
                    shell=True,
                    cwd=_START_CWD,
                    env=os.environ.copy(),
                    input=(sudo_password + "\n").encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    executable="/bin/bash",
                )

                out = proc.stdout.decode("utf-8", errors="replace")
                return _sanitize_output(out, sudo_password, None)

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