"""Run directly with .venv Python; no database or inference requests."""

import sys
from pathlib import Path
import unittest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_models_doctor import inspect_ollama  # noqa: E402


class InventoryTests(unittest.TestCase):
    def probe(self, response):
        def handle(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/tags")
            self.assertNotIn("authorization", request.headers)
            return response

        return inspect_ollama("http://127.0.0.1:11434", {"chat", "embed"}, transport=httpx.MockTransport(handle))

    def test_missing_model(self):
        result = self.probe(httpx.Response(200, json={"models": [{"name": "chat"}]}))
        self.assertEqual(result["missing"], ["embed"])
        self.assertFalse(result["inference_verified"])

    def test_complete_inventory_is_not_inference(self):
        result = self.probe(httpx.Response(200, json={"models": [{"name": n} for n in ("chat", "embed")]}))
        self.assertEqual(result["state"], "models_present")
        self.assertFalse(result["inference_verified"])

    def test_invalid_and_redirect(self):
        for response, state in [
            (httpx.Response(200, json=[]), "invalid_response"),
            (httpx.Response(302, headers={"location": "https://example.com"}), "http_error"),
            (httpx.Response(200, content=b"x" * (1024 * 1024 + 1)), "response_too_large"),
        ]:
            self.assertEqual(self.probe(response)["state"], state)

    def test_reject_remote_before_transport(self):
        def forbidden(request):
            self.fail("network must not be reached")

        result = inspect_ollama("https://example.com", {"chat"}, transport=httpx.MockTransport(forbidden))
        self.assertEqual(result["state"], "invalid_local_endpoint")


if __name__ == "__main__":
    unittest.main()
