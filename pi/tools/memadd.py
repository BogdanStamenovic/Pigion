def memadd(command, memory, local_state):
    local_state=dict(local_state)  # Make a copy to avoid mutating the original
    existing_lines = [line for line in memory.splitlines() if line.strip()]
    if existing_lines and existing_lines[-1] == command:
        new_memory = memory
        output = "MEMORY_ALREADY_ENDED_WITH_SAME_command"
    elif "=" in command:
            
        key, val = command.split("=", 1)
        local_state["MEMORYVALS"][key] = val
        new_memory = memory
        output = f"MEMORY_KEY_UPDATED: {key} to {val}"
    else:
        new_memory = (memory + "\n" + command).strip() if memory else command
        output = "MEMORY_UPDATED"

    local_state["last_tool_output"] = output
    return {
            "ok": True,
            "output": output,
            "memory": new_memory,
            "state": local_state,
        }
