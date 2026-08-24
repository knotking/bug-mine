"""GitHub App — pull request webhook to Check Run.

The design's headline use case: a developer opens a PR and sees which known defects their change
actually reaches, on the diff, before it merges. That is the moment the information is worth
something; a report read three weeks later is not.

Three properties this has to get right, all of them about *not* lying:

- **A Check Run that says nothing reads as approval.** So an absent catalog entry is reported as
  "not covered" explicitly (FR-39), and the count of suppressed matches is shown — a scan that
  narrowed everything away must not look like one that found nothing.
- **Every conclusion is grounded.** A finding cites the record behind it, and the Check Run
  links to it. An unlinked claim is one a reviewer cannot check.
- **A webhook is untrusted input.** The signature is verified before the body is parsed, and
  compared in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

# GitHub signs the raw body; anything that reserialises it before checking will not match.
SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"

# Only these actions warrant a scan. `synchronize` is a new push to an open PR, which is the
# case that matters most — the diff changed, so the answer may have too.
SCANNED_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})


class WebhookError(Exception):
    """Raised with a stable code. Callers branch on the code, never the message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def verify_signature(body: bytes, signature: str | None, secret: str) -> None:
    """Verify a webhook came from GitHub.

    Constant-time comparison: a timing-variable one leaks the expected digest a byte at a time,
    and a forged webhook can start a scan against any repository we can reach.
    """
    if not secret:
        raise WebhookError("no_secret", "Webhook secret is not configured.")
    if not signature or not signature.startswith("sha256="):
        raise WebhookError("missing_signature", "Request is not signed.")

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("bad_signature", "Signature does not match.")


@dataclass(frozen=True)
class PullRequestEvent:
    action: str
    repo_full_name: str
    clone_url: str
    head_sha: str
    installation_id: int | None
    number: int

    @property
    def should_scan(self) -> bool:
        return self.action in SCANNED_ACTIONS


def parse_pull_request(payload: dict[str, Any]) -> PullRequestEvent:
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    head = pr.get("head") or {}
    return PullRequestEvent(
        action=payload.get("action") or "",
        repo_full_name=repo.get("full_name") or "",
        # The head repo, not the base: a fork's PR must be scanned as the fork, or we scan code
        # that is not the code being proposed.
        clone_url=(head.get("repo") or {}).get("clone_url") or repo.get("clone_url") or "",
        head_sha=head.get("sha") or "",
        installation_id=(payload.get("installation") or {}).get("id"),
        number=pr.get("number") or 0,
    )


def app_jwt(app_id: str, private_key_pem: str, *, now: int | None = None) -> str:
    """A short-lived JWT identifying the app itself, used to mint an installation token.

    Ten minutes is GitHub's maximum and this is a machine-to-machine hop, so there is no reason
    to ask for longer.
    """
    import jwt as pyjwt

    issued = (now or int(time.time())) - 60  # backdated for clock skew between us and GitHub
    return pyjwt.encode(
        {"iat": issued, "exp": issued + 600, "iss": app_id},
        private_key_pem,
        algorithm="RS256",
    )


@dataclass(frozen=True)
class CheckRunSummary:
    """What the reviewer sees. Built from the scan, never from a model."""

    findings: int
    suppressed: int
    uncovered: int
    unresolved: int

    @property
    def conclusion(self) -> str:
        """`neutral` rather than `success` when nothing was found but coverage was partial.

        A green check on a repository we know nothing about is a claim we cannot support, and it
        is the one a reviewer will act on without reading further.
        """
        if self.findings:
            return "action_required"
        if self.uncovered or self.unresolved:
            return "neutral"
        return "success"

    @property
    def title(self) -> str:
        if self.findings:
            return f"{self.findings} finding{'s' if self.findings != 1 else ''} reach this code"
        if self.uncovered or self.unresolved:
            return "No findings, but coverage is partial"
        return "No known defects reach this code"


def render_summary(summary: CheckRunSummary, findings: list[dict[str, Any]]) -> str:
    """The Check Run body, in Markdown.

    Every line a reviewer needs to judge it: what reaches them, what was narrowed away, and what
    we could not speak to at all.
    """
    lines: list[str] = []

    if summary.findings:
        lines.append(f"**{summary.findings} known defect(s) reach this code.**\n")
        for f in findings[:25]:
            mark = "" if f.get("reachable") else " _(reachability undetermined)_"
            cite = f.get("citations") or []
            entry = f"- **{f.get('component')}** — {f.get('title', '')[:140]}{mark}"
            if f.get("detail"):
                # Where it lands, or why reachability could not decide. Either way it is what
                # lets a reviewer judge the finding rather than take it on trust.
                entry += f"  \n  `{f.get('detail', '')[:160]}`"
            if cite:
                entry += f"  \n  cites record version `{cite[0]}`"
            lines.append(entry)
        if len(findings) > 25:
            lines.append(f"\n_…and {len(findings) - 25} more._")
    else:
        lines.append("**No known defects reach this code.**\n")

    lines.append("")
    if summary.suppressed:
        lines.append(
            f"{summary.suppressed} catalog match(es) were **suppressed** — the defect exists in "
            "a dependency, but this code does not touch it."
        )
    if summary.uncovered:
        lines.append(
            f"⚠️ {summary.uncovered} dependenc(ies) are **not covered** by the catalog. That "
            "means we know nothing about them, not that they are healthy."
        )
    if summary.unresolved:
        lines.append(
            f"⚠️ {summary.unresolved} manifest(s) declare dependencies without pinning them, so "
            "their versions could not be resolved and they were not checked."
        )
    return "\n".join(lines)
