from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber.dashboard import (
    DASHBOARD_TOKEN,
    dashboard_auth_middleware,
    init_dashboard_routes,
)


class DashboardAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.middleware("http")(dashboard_auth_middleware)
        app.include_router(init_dashboard_routes())
        self.client = TestClient(app)

    def test_api_requires_auth(self):
        response = self.client.get("/api/dashboard/session")
        self.assertEqual(response.status_code, 401)

    def test_login_sets_cookie_and_unlocks_api(self):
        response = self.client.post(
            "/api/dashboard/login", json={"token": DASHBOARD_TOKEN}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("dsh_dashboard", response.cookies)
        session = self.client.get("/api/dashboard/session")
        self.assertEqual(session.status_code, 200)
        self.assertTrue(session.json()["ok"])

    def test_wrong_token_is_rejected(self):
        response = self.client.post(
            "/api/dashboard/login", json={"token": "not-the-token"}
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
