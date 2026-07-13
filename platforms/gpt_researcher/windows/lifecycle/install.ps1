$ErrorActionPreference = "Stop"
$packages = @("gpt-researcher", "langchain-ollama", "playwright", "trafilatura")
if ($env:GPT_RESEARCHER_EXTRACTION -eq "crawl4ai") { $packages += "crawl4ai" }
if ($env:GPT_RESEARCHER_QDRANT_URL) { $packages += "qdrant-client" }
& $env:PIGION_VENV_PYTHON -m pip install @packages
if ($LASTEXITCODE -ne 0) { throw "GPT Researcher dependency installation failed" }
& $env:PIGION_VENV_PYTHON -m playwright install $(if ($env:GPT_RESEARCHER_PLAYWRIGHT_BROWSER) { $env:GPT_RESEARCHER_PLAYWRIGHT_BROWSER } else { "chromium" })
if ($LASTEXITCODE -ne 0) { throw "Playwright browser installation failed" }
