def research(command, memory, local_state, program_state=None):
    program_state = program_state or {}
    local_state = dict(local_state)
    local_state["last_research_topic"] = command
    return {
        "ok": True,
        "output": "The GPT Researcher wrapper handles research goals through run_agent().",
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
    }
