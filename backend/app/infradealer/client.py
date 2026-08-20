"""Central InfraDealer HTTP API client with HMAC request signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

import httpx

from .events import API_METHODS, endpoint_url

log = logging.getLogger("infradealer.integration.client")


class InfraDealerApiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        api_version: str = "v1",
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.api_version = (api_version or "v1").strip().lstrip("/")
        self.timeout = timeout

    @classmethod
    def from_integration(cls, row, timeout: float = 30.0) -> "InfraDealerApiClient":
        from .crypto import decrypt_secret

        return cls(
            base_url=row.base_url,
            api_key=decrypt_secret(row.api_key_enc),
            api_secret=decrypt_secret(row.api_secret_enc),
            api_version=row.api_version or "v1",
            timeout=timeout,
        )

    def _url(self, api_event: str) -> str:
        return endpoint_url(self.base_url, api_event)

    def request(
        self,
        api_event: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        method: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        verb = (method or API_METHODS.get(api_event) or "POST").upper()
        raw = "" if verb == "GET" else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        url = self._url(api_event)
        headers = self._headers(rid, raw)
        safe_headers = {k: v for k, v in headers.items() if "signature" not in k.lower()}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if verb == "GET":
                    res = client.get(url, headers=headers, params=query or None)
                else:
                    res = client.post(url, content=raw.encode(), headers=headers)
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:
                body = res.json() if res.content else {}
            except json.JSONDecodeError:
                body = {"raw": res.text[:2000]}
            return {
                "ok": 200 <= res.status_code < 300,
                "http_status": res.status_code,
                "body": body if isinstance(body, dict) else {"data": body},
                "request_id": rid,
                "latency_ms": latency_ms,
                "safe_headers": safe_headers,
                "url": url,
                "error": "",
            }
        except httpx.TimeoutException:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": False,
                "http_status": 0,
                "body": {},
                "request_id": rid,
                "latency_ms": latency_ms,
                "safe_headers": safe_headers,
                "url": url,
                "error": "timeout",
            }
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            log.warning("InfraDealer HTTP error: %s", exc)
            return {
                "ok": False,
                "http_status": 0,
                "body": {},
                "request_id": rid,
                "latency_ms": latency_ms,
                "safe_headers": safe_headers,
                "url": url,
                "error": str(exc)[:200],
            }

    def _sign(self, timestamp: str, request_id: str, raw_body: str) -> str:
        msg = f"{timestamp}.{request_id}.{raw_body}".encode()
        digest = hmac.new(self.api_secret.encode(), msg, hashlib.sha256).hexdigest()
        return digest

    def _headers(self, request_id: str, raw_body: str) -> dict[str, str]:
        ts = str(int(time.time()))
        sig = self._sign(ts, request_id, raw_body) if self.api_secret else ""
        headers = {
            "Content-Type": "application/json",
            "X-InfraDealer-Key": self.api_key,
            "X-InfraDealer-Timestamp": ts,
            "X-InfraDealer-Request-ID": request_id,
        }
        if sig:
            headers["X-InfraDealer-Signature"] = sig
        return headers

    def test_connection(self) -> dict[str, Any]:
        payload = {
            "request_id": str(uuid.uuid4()),
            "event": "connection.test",
            "source": "whatsapp_webhook",
        }
        return self.request("connection.test", payload, request_id=payload["request_id"])

    def check_account(self, phone: str, request_id: str | None = None) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        return self.request(
            "account.check",
            {"request_id": rid, "event": "account.check", "customer": {"phone": phone}},
            request_id=rid,
        )

    def create_account(self, name: str, phone: str, request_id: str | None = None) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        return self.request(
            "account.create",
            {
                "request_id": rid,
                "event": "account.create",
                "customer": {"name": name, "phone": phone},
                "source": "whatsapp_ai",
            },
            request_id=rid,
        )

    def verify_otp(
        self,
        registration_id: str,
        phone: str,
        otp: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        return self.request(
            "otp.verify",
            {
                "request_id": rid,
                "event": "otp.verify",
                "registration_id": registration_id,
                "phone": phone,
                "otp": otp,
            },
            request_id=rid,
        )

    def request_otp(self, registration_id: str, phone: str, request_id: str | None = None) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        return self.request(
            "otp.request",
            {
                "request_id": rid,
                "event": "otp.request",
                "registration_id": registration_id,
                "phone": phone,
            },
            request_id=rid,
        )

    def upload_media(self, payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        body = dict(payload)
        body.setdefault("request_id", rid)
        body.setdefault("event", "media.push")
        return self.request("media.push", body, request_id=rid)

    def push_listing(self, payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        rid = request_id or str(uuid.uuid4())
        body = dict(payload)
        body.setdefault("request_id", rid)
        body.setdefault("event", "listing.push")
        body.setdefault("source", "whatsapp_ai")
        return self.request("listing.push", body, request_id=rid)

    def get_status(
        self,
        *,
        request_id: str = "",
        listing_id: str = "",
        rid: str | None = None,
    ) -> dict[str, Any]:
        query_rid = rid or request_id or str(uuid.uuid4())
        query: dict[str, Any] = {"request_id": query_rid}
        if request_id:
            query["request_id"] = request_id
        if listing_id:
            query["listing_id"] = listing_id
        return self.request("status", {}, request_id=query_rid, method="GET", query=query)
