"""Postgres staging store for local verify DB only."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from tenant_release.store import StageGuardError, utcnow, validate_stage_dsn


class PostgresStagingStore:
    def __init__(self, dsn: str) -> None:
        validate_stage_dsn(dsn)
        self.dsn = dsn

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise StageGuardError("psycopg is required for postgres store") from exc
        conn = psycopg.connect(self.dsn, row_factory=dict_row)
        conn.execute("SET search_path TO public")
        return conn

    def find_run(self, package_id: str, package_sha256: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select run_id::text, package_id, package_version, tenant_id, package_sha256
                from tenant_release_run
                where package_id = %s and package_sha256 = %s
                limit 1
                """,
                (package_id, package_sha256),
            ).fetchone()
        return dict(row) if row else None

    def insert_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.find_run(payload["package_id"], payload["package_sha256"])
        if existing:
            return existing
        run_id = payload.get("run_id") or str(uuid4())
        products = list(payload.get("products") or [])
        with self._connect() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    insert into tenant_release_run (
                      run_id, package_id, package_version, tenant_id, package_sha256, release_status
                    ) values (%s, %s, %s, %s, %s, %s)
                    on conflict (package_id, package_sha256) do nothing
                    """,
                    (
                        run_id,
                        payload["package_id"],
                        payload["package_version"],
                        payload["tenant_id"],
                        payload["package_sha256"],
                        payload.get("release_status") or "candidate",
                    ),
                )
                found = conn.execute(
                    """
                    select run_id::text from tenant_release_run
                    where package_id = %s and package_sha256 = %s
                    """,
                    (payload["package_id"], payload["package_sha256"]),
                ).fetchone()
                actual_id = str(found["run_id"]) if found else run_id
                for product in products:
                    conn.execute(
                        """
                        insert into tenant_release_staging_product (
                          run_id, tenant_id, sku, canonical_name, review_status,
                          retail_price_byn, partner_price_byn, partner_w, price_missing,
                          media_state, payload
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        on conflict (run_id, sku) do nothing
                        """,
                        (
                            actual_id,
                            payload["tenant_id"],
                            product.get("sku"),
                            product.get("canonical_name"),
                            product.get("review_status") or "candidate",
                            product.get("retail_price_byn"),
                            product.get("partner_price_byn"),
                            product.get("partner_w"),
                            bool(product.get("price_missing")),
                            product.get("media_state"),
                            json.dumps(product, ensure_ascii=False),
                        ),
                    )
        return {"run_id": actual_id, **{k: payload[k] for k in ("package_id", "package_version", "tenant_id", "package_sha256") if k in payload}}

    def product_count(self, run_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*)::int as n from tenant_release_staging_product where run_id = %s",
                (run_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def find_current_candidate(self, tenant_id: str, package_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select candidate_id::text, run_id::text, package_id, package_version,
                       tenant_id, package_sha256, status
                from tenant_release_candidate
                where tenant_id = %s and package_id = %s and status = 'current'
                limit 1
                """,
                (tenant_id, package_id),
            ).fetchone()
        return dict(row) if row else None

    def insert_candidate(self, payload: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
        current = self.find_current_candidate(payload["tenant_id"], payload["package_id"])
        if current and current.get("package_sha256") == payload["package_sha256"]:
            return {**current, "reused": True, "superseded": []}
        candidate_id = payload.get("candidate_id") or str(uuid4())
        superseded: list[str] = []
        with self._connect() as conn:
            with conn.transaction():
                if current:
                    conn.execute(
                        """
                        update tenant_release_candidate
                        set status = 'superseded'
                        where candidate_id = %s and status = 'current'
                        """,
                        (current["candidate_id"],),
                    )
                    superseded.append(str(current["candidate_id"]))
                try:
                    conn.execute(
                        """
                        insert into tenant_release_candidate (
                          candidate_id, run_id, package_id, package_version,
                          tenant_id, package_sha256, status
                        ) values (%s, %s, %s, %s, %s, %s, 'current')
                        """,
                        (
                            candidate_id,
                            payload["run_id"],
                            payload["package_id"],
                            payload["package_version"],
                            payload["tenant_id"],
                            payload["package_sha256"],
                        ),
                    )
                except Exception as exc:
                    message = str(exc).lower()
                    if "tenant_release_candidate_one_current" in message or "unique" in message:
                        raise StageGuardError("parallel current candidate rejected") from exc
                    raise
                for product in products:
                    conn.execute(
                        """
                        insert into tenant_release_candidate_product (
                          candidate_id, tenant_id, sku, canonical_name, review_status,
                          retail_price_byn, partner_price_byn, partner_w, price_missing,
                          media_state, card_present, source
                        ) values (%s, %s, %s, %s, 'approved', %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            candidate_id,
                            payload["tenant_id"],
                            product["sku"],
                            product["canonical_name"],
                            product.get("retail_price_byn"),
                            product.get("partner_price_byn"),
                            product.get("partner_w"),
                            bool(product.get("price_missing")),
                            product.get("media_state") or "missing",
                            bool(product.get("card_present")),
                            json.dumps(product.get("source") or {}, ensure_ascii=False),
                        ),
                    )
        return {
            "candidate_id": candidate_id,
            "status": "current",
            "reused": False,
            "superseded": superseded,
            "created_at": utcnow(),
            **payload,
        }
