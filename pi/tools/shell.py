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

# Main persistent shell
_SHELL_PID = None
_SHELL_FD = None

# Branch shell used for interactive programs
_BRANCH_PID = None
_BRANCH_FD = None
_BRANCH_MARKER = None
_BRANCH_SESSION_LABEL = None

_COUNTER = 0
_LOCK = threading.Lock()

# Captured interactive session state
_INTERACTIVE_MODE = False

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

COMMAND_WRAPPERS = {
    "sudo",
    "sshpass",
    "env",
    "timeout",
    "nohup",
    "setsid",
    "stdbuf",
    "unbuffer",
    "proxychains",
    "proxychains4",
    "tsocks",
    "nice",
    "ionice",
    "time",
    "doas",
}

INTERACTIVE_LAUNCHERS = {
    "ssh", "scp", "sftp", "telnet", "ftp", "rlogin", "rsh",
    "python", "python3", "ipython", "pypy", "node", "ruby", "perl",
    "php", "lua", "irb", "r",
    "bash", "sh", "zsh", "fish", "ksh", "dash",
    "mysql", "mariadb", "psql", "sqlite3", "redis-cli", "mongo", "mongosh",
    "gdb", "lldb", "btop", "htop", "top", "less", "more", "vim", "vi",
    "nano", "emacs", "tmux", "screen", "bettercap", "nmap", "nc", "netcat",
}

WRAPPER_VALUE_OPTS = {
    "sudo": {"-u": 1, "-g": 1, "-p": 1, "-k": 0, "-n": 0, "-S": 0, "-H": 0, "-E": 0},
    "sshpass": {"-p": 1, "-f": 1, "-d": 1, "-e": 0},
    "timeout": {"-k": 1, "-s": 1},
    "env": {"-u": 1, "-i": 0},
    "nice": {"-n": 1},
    "ionice": {"-c": 1, "-n": 1, "-p": 1},
    "stdbuf": {"-i": 1, "-o": 1, "-e": 1},
    "doas": {"-u": 1},
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


def _shell_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _drain_fd(fd: Optional[int], timeout: float = 0.05) -> str:
    if fd is None:
        return ""
    chunks = []
    while True:
        r, _, _ = select.select([fd], [], [], timeout)
        if not r:
            break
        try:
            chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
        except OSError:
            break
        chunks.append(chunk)
    return "".join(chunks)


def _spawn_pty_shell(cwd: Optional[str] = None):
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd or _START_CWD)
        except Exception:
            try:
                os.chdir(_START_CWD)
            except Exception:
                pass

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

    return pid, fd


def _prepare_shell_fd(fd: int) -> None:
    os.write(fd, b"export PS1=''\n")
    os.write(fd, b"stty -echo\n")
    _drain_fd(fd)


def _start_shell() -> None:
    global _SHELL_PID, _SHELL_FD
    if _shell_alive(_SHELL_PID):
        return
    _SHELL_PID, _SHELL_FD = _spawn_pty_shell(_START_CWD)
    _prepare_shell_fd(_SHELL_FD)


def _start_branch_shell(cwd: Optional[str] = None) -> None:
    global _BRANCH_PID, _BRANCH_FD, _BRANCH_MARKER
    if _shell_alive(_BRANCH_PID):
        return
    _BRANCH_PID, _BRANCH_FD = _spawn_pty_shell(cwd or _START_CWD)
    _BRANCH_MARKER = f"__BRANCH_DONE_{time.time_ns()}__"
    _prepare_shell_fd(_BRANCH_FD)


def _reset_branch_shell() -> None:
    global _BRANCH_PID, _BRANCH_FD, _BRANCH_MARKER, _BRANCH_SESSION_LABEL
    pid = _BRANCH_PID
    fd = _BRANCH_FD

    _BRANCH_PID = None
    _BRANCH_FD = None
    _BRANCH_MARKER = None
    _BRANCH_SESSION_LABEL = None

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


def _escape_single_quotes(s: str) -> str:
    return s.replace("'", "'\"'\"'")


def _strip_ansi(s: str) -> str:
    try:
        return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", s)
    except re.error:
        return s


def _sanitize_output(s: str, sudo_password: Optional[str], sudo_prompt: Optional[str]) -> str:
    if not s:
        return ""

    s = _strip_ansi(s)
    s = re.sub(r"__CMD_DONE_\d+__", "", s)
    s = re.sub(r"__BRANCH_DONE_\d+__", "", s)

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


def _extract_cwd_marker(output: str, marker: str) -> tuple[str, Optional[str]]:
    if not output or not marker:
        return output, None

    cwd = None
    kept_lines = []
    for line in output.splitlines():
        if line.startswith(marker):
            cwd = line[len(marker):].strip() or cwd
            continue
        kept_lines.append(line)

    return "\n".join(kept_lines).strip(), cwd


def _load_dotenv(path: str) -> None:
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


def _fallback_session_label(command_str: str) -> str:
    peeled = _peel_wrappers(command_str)
    base = peeled.get("base_token") or ""
    if not base:
        base = "interactive"
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_") or "interactive"
    return f"{base}_interactive"


def _branch_session_label(command_str: str, output: str) -> str:
    extracted = _extract_session_label(output)
    fallback = _fallback_session_label(command_str)
    if not extracted or extracted == "@interactive":
        return fallback
    return extracted


def _decorate_cwd(cwd: str, label: Optional[str]) -> str:
    if not cwd:
        return cwd
    if not label:
        return cwd
    prefix = f"{label} - "
    return cwd if cwd.startswith(prefix) else f"{prefix}{cwd}"


def _is_session_label(label: str) -> bool:
    label = label.strip()
    if not label:
        return False
    return label.startswith("@") or bool(re.fullmatch(r"[A-Za-z0-9_.-]+_interactive", label))


def _undecorate_cwd(cwd: str) -> str:
    if not cwd:
        return cwd
    result = cwd.strip()
    while " - " in result:
        label, rest = result.split(" - ", 1)
        if not _is_session_label(label):
            break
        result = rest.strip()
    return result


def _extract_path_candidate(text: str) -> Optional[str]:
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


def _peel_wrappers(command_str: str) -> Dict[str, Any]:
    original_tokens = _tokenize_command(command_str)
    tokens = list(original_tokens)
    wrapper_chain: List[str] = []

    changed = True
    while tokens and changed:
        changed = False

        if tokens[0] in ("sh", "bash", "zsh") and "-c" in tokens:
            try:
                cidx = tokens.index("-c")
                inner = tokens[cidx + 1] if cidx + 1 < len(tokens) else ""
                wrapper_chain.append(tokens[0])
                try:
                    tokens = _tokenize_command(inner)
                except Exception:
                    tokens = inner.split()
                changed = True
                continue
            except Exception:
                pass

        head = tokens[0]

        if head in COMMAND_WRAPPERS:
            wrapper_chain.append(head)
            tokens = tokens[1:]
            tokens = _strip_leading_option_values(tokens, head)
            changed = True
            continue

        if head in ("proxychains", "proxychains4", "tsocks", "nohup", "setsid", "time", "unbuffer"):
            wrapper_chain.append(head)
            tokens = tokens[1:]
            tokens = _strip_leading_option_values(tokens, head)
            changed = True
            continue

        if head in ("command", "builtin") and len(tokens) > 1:
            wrapper_chain.append(head)
            tokens = tokens[1:]
            changed = True
            continue

    base_token = tokens[0] if tokens else ""
    return {
        "original_tokens": original_tokens,
        "peeled_tokens": tokens,
        "base_token": base_token,
        "wrapper_chain": wrapper_chain,
    }


def _tokenize_command(command_str: str) -> List[str]:
    try:
        return shlex.split(command_str)
    except Exception:
        return command_str.split()


def _strip_leading_option_values(tokens: List[str], wrapper: str) -> List[str]:
    if not tokens:
        return tokens

    out = list(tokens)

    if wrapper == "env":
        while out:
            t = out[0]
            if t.startswith("-"):
                opt = out.pop(0)
                if WRAPPER_VALUE_OPTS["env"].get(opt, 0) and out:
                    out.pop(0)
                continue
            if "=" in t and not t.startswith(("ssh://", "http://", "https://")):
                out.pop(0)
                continue
            break
        return out

    if wrapper == "timeout":
        while out and out[0].startswith("-"):
            opt = out.pop(0)
            if WRAPPER_VALUE_OPTS["timeout"].get(opt, 0) and out:
                out.pop(0)
        if out and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", out[0]):
            out.pop(0)
        return out

    while out and out[0].startswith("-"):
        opt = out.pop(0)
        if WRAPPER_VALUE_OPTS.get(wrapper, {}).get(opt, 0) and out:
            out.pop(0)

    return out


def _should_use_branch(command_str: str) -> bool:
    if not command_str:
        return False

    peeled = _peel_wrappers(command_str)
    tokens = peeled["peeled_tokens"]
    if not tokens:
        return False

    tok = tokens[0]

    if tok in ("sh", "bash", "zsh") and "-c" in tokens:
        try:
            cidx = tokens.index("-c")
            inner_cmd = tokens[cidx + 1] if cidx + 1 < len(tokens) else ""
            inner = _tokenize_command(inner_cmd)
            if inner:
                tok = inner[0]
        except Exception:
            pass

    if tok in INTERACTIVE_LAUNCHERS:
        return True

    if any(flag in tokens for flag in ("-i", "--interactive")):
        return True

    return False


def _send_to_fd(fd: Optional[int], text: str) -> None:
    if fd is None:
        raise RuntimeError("Shell is not initialized.")

    if text is None:
        text = ""

    if text == "":
        os.write(fd, b"\n")
        return

    for line in text.splitlines():
        os.write(fd, (line + "\n").encode("utf-8"))


def _read_until_marker_or_idle_fd(
    fd: Optional[int],
    pid: Optional[int],
    marker: str,
    timeout: float = 0.2,
    stream: bool = True,
    truncate: bool = False,
    truncate_policy: Optional[dict] = None,
    tail_lines: Optional[int] = None,
) -> Dict[str, Any]:
    if fd is None:
        return {"output": "", "completed": False, "interactive_mode": False}

    chunks: List[str] = []
    buffer = ""
    last_activity = time.monotonic()
    prompt_seen = False

    while True:
        r, _, _ = select.select([fd], [], [], timeout)

        if not r:
            idle_for = time.monotonic() - last_activity

            if prompt_seen and _shell_alive(pid) and idle_for >= INTERACTIVE_PROMPT_GRACE_SECONDS:
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

            if _shell_alive(pid) and idle_for >= INTERACTIVE_IDLE_SECONDS:
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
            chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
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
                to_print = re.sub(r"(?m)^__CMD_CWD_\d+__.*(?:\n|$)", "", to_print)
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

        if not _shell_alive(pid):
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
    cwd: Optional[str] = None,
):
    global _COUNTER, _INTERACTIVE_MODE, _BRANCH_MARKER, _BRANCH_SESSION_LABEL

    with _LOCK:
        _start_shell()

        branch_alive = _shell_alive(_BRANCH_PID)
        continuing_interactive = _INTERACTIVE_MODE and branch_alive and (_BRANCH_FD is not None)

        if continuing_interactive:
            # User-requested branch termination keyword.
            if isinstance(command, str) and command.strip().lower() == "done":
                _reset_branch_shell()
                _INTERACTIVE_MODE = False

                return {
                    "output": "[interactive branch terminated]",
                    "interactive_mode": False,
                    "completed": True,
                    "branch_mode": False,
                    "session_label": None,
                    "cwd_raw": None,
                    "cwd_display": None,
                }
            SPECIAL_KEYS = {
                "SIGINT": b"\x03",
                "EOF": b"\x04",
                "SIGTSTP": b"\x1A",
            }

            if command in SPECIAL_KEYS:
                os.write(_BRANCH_FD, SPECIAL_KEYS[command])
            else:
                _send_to_fd(_BRANCH_FD, command)

            marker = _BRANCH_MARKER or "__BRANCH_DONE__"
            read_result = _read_until_marker_or_idle_fd(
                fd=_BRANCH_FD,
                pid=_BRANCH_PID,
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
                _reset_branch_shell()
                _INTERACTIVE_MODE = False
            else:
                _INTERACTIVE_MODE = True

            return {
                "output": output,
                "interactive_mode": _INTERACTIVE_MODE,
                "completed": completed,
                "branch_mode": True,
                "session_label": _BRANCH_SESSION_LABEL,
                "cwd_raw": None,
                "cwd_display": _decorate_cwd(cwd or _START_CWD, _BRANCH_SESSION_LABEL),
            }

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
                processed = _apply_truncation(raw_out, policy, tail_lines, force_tail=truncate) if should_trunc else raw_out

                return {
                    "output": _sanitize_output(processed, sudo_password, None),
                    "interactive_mode": False,
                    "completed": True,
                    "branch_mode": False,
                    "session_label": None,
                    "cwd_raw": None,
                    "cwd_display": None,
                }

            escaped = _escape_single_quotes(rest_command)
            opt_str = (" " + " ".join(options)) if options else ""
            wrapped = f"sudo -n{opt_str} bash -c '{escaped}'"
            _send_to_fd(_SHELL_FD, wrapped)

            marker = f"__CMD_DONE_{_COUNTER + 1}__"
            _COUNTER += 1
            os.write(_SHELL_FD, f'printf "{marker}\\n"\n'.encode("utf-8"))

            read_result = _read_until_marker_or_idle_fd(
                fd=_SHELL_FD,
                pid=_SHELL_PID,
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

            if interactive_mode and not completed:
                _INTERACTIVE_MODE = False

            return {
                "output": output,
                "interactive_mode": interactive_mode,
                "completed": completed,
                "branch_mode": False,
                "session_label": None,
                "cwd_raw": None,
                "cwd_display": None,
            }

        use_branch = _should_use_branch(command)

        if use_branch:
            branch_cwd = _undecorate_cwd(cwd) if cwd else _START_CWD
            _start_branch_shell(branch_cwd)

            for line in command.splitlines():
                os.write(_BRANCH_FD, (line + "\n").encode("utf-8"))

            os.write(_BRANCH_FD, f'printf "{_BRANCH_MARKER}\\n"\n'.encode("utf-8"))

            read_result = _read_until_marker_or_idle_fd(
                fd=_BRANCH_FD,
                pid=_BRANCH_PID,
                marker=_BRANCH_MARKER,
                timeout=timeout,
                stream=stream,
                truncate=truncate,
                truncate_policy=truncate_policy,
                tail_lines=tail_lines,
            )

            output = read_result["output"]
            completed = bool(read_result["completed"])
            interactive_mode = bool(read_result["interactive_mode"])

            branch_label = _branch_session_label(command, output)
            _BRANCH_SESSION_LABEL = branch_label

            if completed:
                _reset_branch_shell()
                _INTERACTIVE_MODE = False
            else:
                _INTERACTIVE_MODE = True

            return {
                "output": output,
                "interactive_mode": _INTERACTIVE_MODE,
                "completed": completed,
                "branch_mode": True,
                "session_label": branch_label,
                "cwd_raw": branch_cwd,
                "cwd_display": _decorate_cwd(branch_cwd, branch_label),
            }

        _COUNTER += 1
        marker = f"__CMD_DONE_{_COUNTER}__"
        cwd_marker = f"__CMD_CWD_{_COUNTER}__"

        for line in command.splitlines():
            os.write(_SHELL_FD, (line + "\n").encode("utf-8"))

        os.write(_SHELL_FD, f'printf "{cwd_marker}%s\\n" "$PWD"\n'.encode("utf-8"))
        os.write(_SHELL_FD, f'printf "{marker}\\n"\n'.encode("utf-8"))

        read_result = _read_until_marker_or_idle_fd(
            fd=_SHELL_FD,
            pid=_SHELL_PID,
            marker=marker,
            timeout=timeout,
            stream=stream,
            truncate=truncate,
            truncate_policy=truncate_policy,
            tail_lines=tail_lines,
        )

        output = read_result["output"]
        output, cwd_after = _extract_cwd_marker(output, cwd_marker)
        completed = bool(read_result["completed"])
        interactive_mode = bool(read_result["interactive_mode"])

        if completed:
            _INTERACTIVE_MODE = False
        elif interactive_mode:
            _INTERACTIVE_MODE = False

        return {
            "output": output,
            "interactive_mode": interactive_mode,
            "completed": completed,
            "branch_mode": False,
            "session_label": None,
            "cwd_raw": cwd_after if completed else None,
            "cwd_display": None,
        }


def shell_reset():
    global _SHELL_PID, _SHELL_FD, _INTERACTIVE_MODE, _BRANCH_PID, _BRANCH_FD, _BRANCH_MARKER, _BRANCH_SESSION_LABEL

    _reset_branch_shell()

    pid = _SHELL_PID
    fd = _SHELL_FD

    _SHELL_PID = None
    _SHELL_FD = None
    _INTERACTIVE_MODE = False
    _BRANCH_SESSION_LABEL = None

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


def shell(command, memory, local_state, program_state):
    current_cwd = _undecorate_cwd(str(local_state.get("CURRENT_WORKING_DIRECTORY", "")).strip())

    result = run_shell(command, stream=True, truncate=True, cwd=current_cwd)

    output = str(result.get("output", ""))
    interactive_mode = bool(result.get("interactive_mode", False))
    completed = bool(result.get("completed", True))
    branch_mode = bool(result.get("branch_mode", False))
    session_label = result.get("session_label") or None
    cwd_raw = result.get("cwd_raw") or None
    cwd_display = result.get("cwd_display") or None

    # Keep local_state minimal: only CURRENT_WORKING_DIRECTORY.
    if branch_mode and interactive_mode:
        if cwd_display:
            local_state["CURRENT_WORKING_DIRECTORY"] = cwd_display
        else:
            label = session_label or _fallback_session_label(command)
            base_cwd = cwd_raw or current_cwd or _START_CWD
            local_state["CURRENT_WORKING_DIRECTORY"] = _decorate_cwd(base_cwd, label)
    else:
        # restore plain path in local_state
        if cwd_raw:
            local_state["CURRENT_WORKING_DIRECTORY"] = cwd_raw
        elif cwd_display:
            local_state["CURRENT_WORKING_DIRECTORY"] = _undecorate_cwd(cwd_display)
        elif current_cwd:
            local_state["CURRENT_WORKING_DIRECTORY"] = current_cwd
        else:
            local_state["CURRENT_WORKING_DIRECTORY"] = os.getcwd()

    # Program state carries everything else.
    local_state["last_action"] = command
    local_state["last_tool_output"] = output
    program_state["INTERACTIVE_MODE"] = "ON" if interactive_mode else "OFF"
    program_state["INTERACTIVE_COMPLETED"] = completed
    program_state["BRANCH_MODE"] = "ON" if branch_mode else "OFF"
    program_state["ACTIVE_SESSION"] = "BRANCH" if branch_mode and interactive_mode else "MAIN"

    if session_label:
        program_state["SESSION_LABEL"] = session_label
    elif branch_mode and interactive_mode:
        program_state["SESSION_LABEL"] = _fallback_session_label(command)

    if branch_mode and interactive_mode:
        program_state["BRANCH_WORKING_DIRECTORY"] = cwd_raw or _undecorate_cwd(local_state["CURRENT_WORKING_DIRECTORY"])
        program_state["CURRENT_WORKING_DIRECTORY"] = local_state["CURRENT_WORKING_DIRECTORY"]
    else:
        program_state["BRANCH_WORKING_DIRECTORY"] = None
        program_state["CURRENT_WORKING_DIRECTORY"] = local_state["CURRENT_WORKING_DIRECTORY"]
    print(local_state["CURRENT_WORKING_DIRECTORY"])
    return {
        "ok": True,
        "output": output,
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
        "interactive_mode": interactive_mode,
        "completed": completed,
        "branch_mode": branch_mode,
    }
