"""HTTP client for the BugMine API.

Hand-written rather than generated for now — the generated SDKs come from the OpenAPI spec in
CI, but the CLI and MCP server need something today and this is the shape they need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from bugmine.inventory import Dependency

DEFAULT_BASE_URL = "http://localhost:8080"


@dataclass(frozen=True)
class Match:
    name: str
    version: str | None
    ecosystem: str
    bug_type: str
    title: str
    version_confirmed: bool
    evidence_url: str | None
    bug_id: str


@dataclass(frozen=True)
class NotCovered:
    name: str
    ecosystem: str
    reason: str


@dataclass(frozen=True)
class CheckResult:
    matches: list[Match]
    not_covered: list[NotCovered]


class BugMineClient:
    def __init__(
        self, base_url: str | None = None, api_key: str | None = None, timeout: float = 30.0
    ) -> None:
        self.base_url = (base_url or os.environ.get("BUGMINE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("BUGMINE_API_KEY")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("No API key. Set BUGMINE_API_KEY or pass --api-key.")
        return {"Authorization": f"Bearer {self.api_key}"}

    def check_dependencies(
        self, deps: list[Dependency], *, bug_types: list[str] | None = None
    ) -> CheckResult:
        payload: dict[str, Any] = {
            "dependencies": [
                {
                    "ecosystem": d.ecosystem,
                    "name": d.name,
                    "version": d.version,
                    "direct": d.direct,
                }
                for d in deps
            ]
        }
        if bug_types:
            payload["bug_types"] = bug_types

        with httpx.Client(timeout=self._timeout) as http:
            r = http.post(
                f"{self.base_url}/v1/check/dependencies",
                json=payload,
                headers=self._headers(),
            )
            r.raise_for_status()
            body = r.json()

        return CheckResult(
            matches=[
                Match(
                    name=m["dependency"]["name"],
                    version=m["dependency"].get("version"),
                    ecosystem=m["dependency"]["ecosystem"],
                    bug_type=m["bug_type"],
                    title=m["title"],
                    version_confirmed=m["version_confirmed"],
                    evidence_url=m["citation"].get("evidence_url"),
                    bug_id=m["bug_id"],
                )
                for m in body["matches"]
            ],
            not_covered=[
                NotCovered(name=n["name"], ecosystem=n["ecosystem"], reason=n["reason"])
                for n in body["not_covered"]
            ],
        )
