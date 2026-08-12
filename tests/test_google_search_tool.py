from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "platforms"
    / "pigion"
    / "linux"
    / "tools"
    / "google_search.py"
)


def load_tool():
    spec = importlib.util.spec_from_file_location("pigion_test_google_search", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GoogleSearchToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_requires_query_and_api_key(self):
        self.assertFalse(self.tool.run_google_search("")["ok"])
        with patch.dict(os.environ, {}, clear=True):
            result = self.tool.run_google_search("current test query")
        self.assertFalse(result["ok"])
        self.assertIn("GEMINI_API_KEY", result["error"])

    def test_rejects_non_gemini_provider(self):
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "LLM_PROVIDER": "openai"},
            clear=True,
        ):
            result = self.tool.run_google_search("current test query")
        self.assertFalse(result["ok"])
        self.assertIn("LLM_PROVIDER=gemini", result["error"])

    def test_returns_grounded_answer_queries_and_unique_sources(self):
        metadata = SimpleNamespace(
            web_search_queries=["current test query"],
            grounding_chunks=[
                SimpleNamespace(web=SimpleNamespace(title="Primary", uri="https://example.test/a")),
                SimpleNamespace(web=SimpleNamespace(title="Duplicate", uri="https://example.test/a")),
                SimpleNamespace(web=SimpleNamespace(title="Second", uri="https://example.test/b")),
            ],
        )
        response = SimpleNamespace(
            text="Grounded answer.",
            candidates=[SimpleNamespace(grounding_metadata=metadata)],
        )
        request = {}

        class FakeModels:
            def generate_content(self, **kwargs):
                request.update(kwargs)
                return response

        fake_client = SimpleNamespace(models=FakeModels())
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "LLM_PROVIDER": "gemini", "LLM_MODEL": "test-model"},
            clear=True,
        ):
            with patch.object(self.tool.genai, "Client", return_value=fake_client):
                result = self.tool.run_google_search("current test query")

        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Grounded answer.")
        self.assertEqual(result["search_queries"], ["current test query"])
        self.assertEqual([source["url"] for source in result["sources"]], [
            "https://example.test/a",
            "https://example.test/b",
        ])
        self.assertEqual(request["model"], "test-model")
        self.assertIsNotNone(request["config"].tools[0].google_search)


if __name__ == "__main__":
    unittest.main()
