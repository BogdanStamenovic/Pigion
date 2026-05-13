import subprocess
import os
import base64
import sys
import re
import shlex
import time

_POWERSHELL_PROCESS = None
_POWERSHELL_COUNTER = 0

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
}

DEFAULT_TRUNCATION_POLICIES = {}


def _strip_ansi(s: str) -> str:
    try:
        return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", s)
    except re.error:
        return s


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


def _start_persistent_powershell():
    global _POWERSHELL_PROCESS

    if _POWERSHELL_PROCESS is not None and _POWERSHELL_PROCESS.poll() is None:
        return _POWERSHELL_PROCESS

    _POWERSHELL_PROCESS = subprocess.Popen(
        ["powershell", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=os.getcwd(),
    )

    init_script = r'''
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding           = [Console]::OutputEncoding

# Force file-writing defaults to UTF-8
$PSDefaultParameterValues['Out-File:Encoding']    = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8'
'''

    _POWERSHELL_PROCESS.stdin.write(init_script + "\n")
    _POWERSHELL_PROCESS.stdin.flush()

    return _POWERSHELL_PROCESS


def _match_truncation_policy(command_str: str):
    # No special policies defined for Windows wrapper currently.
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


def shell(command, sudo: bool = False, sudo_password: str | None = None, timeout: float = 1.0, stream: bool = True, truncate: bool = False, truncate_policy: dict | None = None, tail_lines: int | None = None):
    global _POWERSHELL_COUNTER, _POWERSHELL_PROCESS

    process = _start_persistent_powershell()
    _POWERSHELL_COUNTER += 1

    marker = f"__DONE_{_POWERSHELL_COUNTER}__"
    command_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")

    payload = f"""
$__cmdBytes = [System.Convert]::FromBase64String("{command_b64}")
$__cmd      = [System.Text.Encoding]::UTF8.GetString($__cmdBytes)
Invoke-Expression $__cmd
Write-Output "{marker}"
"""

    try:
        process.stdin.write(payload)
        process.stdin.flush()
    except Exception:
        _POWERSHELL_PROCESS = None
        process = _start_persistent_powershell()
        process.stdin.write(payload)
        process.stdin.flush()

    output_lines = []

    while True:
        line = process.stdout.readline()

        if line == "":
            if process.poll() is not None:
                _POWERSHELL_PROCESS = None
                break
            continue

        if line.strip() == marker:
            break

        output_lines.append(line)
        if stream:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except Exception:
                pass

    result = "".join(output_lines)

    # Apply truncation/filtering
    policy = truncate_policy if truncate_policy is not None else _match_truncation_policy(command)
    nonempty = [ln for ln in result.splitlines() if ln.strip() != ""]
    should_trunc = (truncate or policy is not None) and (len(nonempty) >= TRUNCATE_MIN_LINES) and (not _is_truncation_exempt(command))
    if should_trunc:
        result = _apply_truncation(result, policy, tail_lines, force_tail=truncate)

    # Sanitize
    result = _strip_ansi(result)
    lines = [ln for ln in result.splitlines() if ln.strip() != ""]
    return "\n".join(lines).strip()


def shell_reset():
    global _POWERSHELL_PROCESS

    process = _POWERSHELL_PROCESS
    _POWERSHELL_PROCESS = None

    if process is None:
        return

    if process.poll() is None:
        try:
            process.stdin.write("exit\n")
            process.stdin.flush()
        except Exception:
            pass

        try:
            process.terminate()
        except Exception:
            pass