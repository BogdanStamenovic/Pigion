
def askuser(command, memory, local_state):
    local_state=dict(local_state)  # Make a copy to avoid mutating the original
    output = str(input(command))
    local_state["last_tool_output"] = output
    return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
    }