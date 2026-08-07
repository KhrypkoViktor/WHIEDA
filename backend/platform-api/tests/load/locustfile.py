"""Locust load profiles for Platform Core (run against staging, not production)."""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task

BASE_HOST = os.environ.get("PLATFORM_LOAD_HOST", "wwc.best")
REF_CODE = os.environ.get("PLATFORM_LOAD_REF", "ladnaya")


class PlatformCoreUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.session_id = f"locust-{uuid.uuid4().hex[:10]}"
        self.headers = {"Host": BASE_HOST, "Content-Type": "application/json"}

    @task(3)
    def public_ref(self) -> None:
        self.client.get(
            f"/api/v1/public/ref/{REF_CODE}",
            headers=self.headers,
            name="/api/v1/public/ref/{code}",
        )

    @task(2)
    def advisor_greeting(self) -> None:
        self.client.post(
            "/api/advisor/query",
            headers=self.headers,
            json={
                "question": "Привет",
                "session_id": self.session_id,
                "ref": REF_CODE,
            },
            name="/api/advisor/query [greeting]",
        )

    @task(1)
    def advisor_price(self) -> None:
        self.client.post(
            "/api/advisor/query",
            headers=self.headers,
            json={
                "question": "Сколько стоит активатор клеток?",
                "session_id": self.session_id,
                "ref": REF_CODE,
                "sku": "M015-00",
            },
            name="/api/advisor/query [price]",
        )

    @task(1)
    def create_lead(self) -> None:
        self.client.post(
            "/api/v1/leads",
            headers=self.headers,
            json={
                "name": "Load Test",
                "contact": "+79990000001",
                "product": "Активатор клеток",
                "idempotency_key": f"locust-{uuid.uuid4().hex}",
                "ref": REF_CODE,
                "country_code": "RU",
            },
            name="/api/v1/leads",
        )
