import subprocess
import os
import base64

_POWERSHELL_PROCESS = None
_POWERSHELL_COUNTER = 0


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


def shell(command):
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

    return "".join(output_lines).rstrip()


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