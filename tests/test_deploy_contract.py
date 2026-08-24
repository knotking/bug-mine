"""Agreement between the deployed configuration and the code it probes.

Terraform and the application are edited in different files by different reasoning, and nothing
fails when they disagree about a path: a startup probe pointed at a route that does not exist
fails the container *silently* into a crash loop, and a deploy check pointed at a path the
Google edge answers itself reports a healthy service as down.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
APP = ROOT / "packages/bugmine/src/bugmine/api/app.py"
RUN_TF = ROOT / "infra/terraform/run.tf"
DEPLOY_SKILL = ROOT / ".claude/skills/deploy/SKILL.md"


def _app_routes() -> set[str]:
    return set(re.findall(r'@app\.get\("([^"]+)"', APP.read_text()))


def test_every_startup_probe_path_is_a_route_the_app_serves() -> None:
    """A probe on a missing path fails the revision, and the failure names the probe rather
    than the typo."""
    probed = set(re.findall(r"http_get\s*\{[^}]*path\s*=\s*\"([^\"]+)\"", RUN_TF.read_text()))
    assert probed, "no startup probe found — did the block move?"
    assert probed <= _app_routes(), f"probed but not served: {sorted(probed - _app_routes())}"


def test_the_app_serves_readyz() -> None:
    """`/healthz` is answered by the Google edge on a *.run.app host and never reaches the
    container, so health has to be reachable somewhere else as well."""
    assert "/readyz" in _app_routes()


def test_the_deploy_check_does_not_curl_a_path_the_edge_intercepts() -> None:
    """This check reported the service down on every deploy it ever ran, because the response
    it read was generated before Cloud Run's serving path."""
    verify = [
        line
        for line in DEPLOY_SKILL.read_text().splitlines()
        if line.strip().startswith("curl") and "api_url" in line
    ]
    assert verify, "the deploy skill no longer curls the api url"
    for line in verify:
        # The trailing comment is allowed to name the intercepted path; the command is not.
        command = line.split("#")[0]
        assert "/healthz" not in command, f"deploy check curls an intercepted path: {command}"
