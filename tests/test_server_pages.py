from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.requests import Request

from server import server


class ServerPageTests(unittest.TestCase):
    def test_register_page_renders_json_profile_placeholder(self):
        request = Request({"type": "http", "method": "GET", "path": "/register", "headers": []})
        with (
            patch.object(server, "require_login"),
            patch.object(server, "valid_framework_runtimes", return_value=([("pigion", "linux")], {})),
        ):
            page = server.register_page(request)

        self.assertIn(
            'placeholder=\'{"physical_constraints":["Mounted indoors; cannot move."]}\'',
            page,
        )


if __name__ == "__main__":
    unittest.main()
