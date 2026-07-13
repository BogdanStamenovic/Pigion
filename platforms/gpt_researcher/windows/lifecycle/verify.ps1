$ErrorActionPreference = "Stop"
& $env:PIGION_VENV_PYTHON -c "import gpt_researcher, playwright, trafilatura"
if ($LASTEXITCODE -ne 0) { throw "GPT Researcher verification failed" }
