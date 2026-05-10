import os
import pty
import select
import signal
import threading

_SHELL_PID = None
_SHELL_FD = None
_COUNTER = 0
_LOCK = threading.Lock()

# Capture cwd from the importing process
_START_CWD = os.getcwd()


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


def shell(command):
    global _COUNTER

    with _LOCK:
        _start_shell()

        _COUNTER += 1
        marker = f"__CMD_DONE_{_COUNTER}__"

        # Send command line-by-line
        for line in command.splitlines():
            os.write(
                _SHELL_FD,
                (line + "\n").encode("utf-8"),
            )

        # Send completion marker
        os.write(
            _SHELL_FD,
            f'printf "{marker}\\n"\n'.encode("utf-8"),
        )

        output = []

        while True:
            r, _, _ = select.select([_SHELL_FD], [], [], 1)

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

            if marker in chunk:
                break

        result = "".join(output)

        # Remove marker and everything after it
        result = result.split(marker)[0]

        return result.strip()


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