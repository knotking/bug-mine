"""OSV ingestion.

Nothing here goes near a model. OSV states the affected ranges outright, so a parser is both
cheaper and more certain — and, more importantly, direction is *read* rather than inferred, so
the inversion that bit the release-note path four times cannot occur here at all.
"""

from __future__ import annotations

from bugmine.models import BugType
from bugmine.worker.osv import parse, to_bugs


def _vuln(**over) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "id": "GHSA-xxxx",
        "aliases": ["CVE-2026-1111"],
        "summary": "Arbitrary URL generation",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "Django"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "1.3"}, {"fixed": "1.3.4"}],
                    }
                ],
            }
        ],
    }
    base.update(over)
    return base


class TestDirection:
    def test_introduced_and_fixed_are_read_not_inferred(self) -> None:
        bug = to_bugs(_vuln())[0]
        assert bug.applicability["introduced_in"] == "1.3"
        assert bug.applicability["fixed_in"] == "1.3.4"

    def test_introduced_zero_means_from_the_beginning(self) -> None:
        """OSV writes "0"; None says the same thing without pretending it is a version."""
        bug = to_bugs(
            _vuln(affected=[{
                "package": {"ecosystem": "PyPI", "name": "django"},
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.0"}]}],
            }])
        )[0]
        assert bug.applicability["introduced_in"] is None
        assert bug.applicability["fixed_in"] == "2.0"

    def test_an_unfixed_advisory_keeps_its_lower_bound(self) -> None:
        bug = to_bugs(
            _vuln(affected=[{
                "package": {"ecosystem": "PyPI", "name": "django"},
                "ranges": [{"events": [{"introduced": "3.1"}]}],
            }])
        )[0]
        assert bug.applicability["introduced_in"] == "3.1"
        assert bug.applicability["fixed_in"] is None

    def test_several_fixed_branches_become_several_records(self) -> None:
        """One advisory often covers two branches — fixed in 1.3.4 and in 2.0.1. Collapsing them
        would over-claim or under-claim depending on which was kept."""
        bugs = to_bugs(
            _vuln(affected=[{
                "package": {"ecosystem": "PyPI", "name": "django"},
                "ranges": [{"events": [
                    {"introduced": "1.3"}, {"fixed": "1.3.4"},
                    {"introduced": "2.0"}, {"fixed": "2.0.1"},
                ]}],
            }])
        )
        assert {(b.applicability["introduced_in"], b.applicability["fixed_in"]) for b in bugs} == {
            ("1.3", "1.3.4"), ("2.0", "2.0.1"),
        }
        assert len({b.identity_key for b in bugs}) == 2, "branches must not collide on identity"


class TestWithdrawal:
    def test_a_withdrawn_advisory_is_not_ingested(self) -> None:
        """An entry the source has disowned must not become a finding, and we would never learn
        it had been pulled."""
        assert to_bugs(_vuln(withdrawn="2026-01-01T00:00:00Z")) == []

    def test_withdrawals_are_counted_rather_than_silently_dropped(self) -> None:
        result = parse({"vulns": [_vuln(withdrawn="2026-01-01T00:00:00Z"), _vuln()]})
        assert result.withdrawn == 1
        assert len(result.bugs) == 1


class TestIdentity:
    def test_the_cve_is_preferred_as_identity(self) -> None:
        """A CVE is what a user searches for and what other tools report. Filing under the GHSA
        means a search for the CVE finds nothing."""
        bug = to_bugs(_vuln())[0]
        assert bug.identity_key.startswith("cve-2026-1111")

    def test_the_osv_id_is_used_when_there_is_no_cve(self) -> None:
        bug = to_bugs(_vuln(aliases=["PYSEC-2026-1"]))[0]
        assert bug.identity_key.startswith("ghsa-xxxx")


class TestShape:
    def test_the_package_name_is_normalised(self) -> None:
        assert to_bugs(_vuln())[0].component_ref == "django"

    def test_records_are_security(self) -> None:
        assert to_bugs(_vuln())[0].bug_type is BugType.SECURITY

    def test_an_uncatalogued_ecosystem_is_skipped(self) -> None:
        """Absent from the map means we do not catalog it — not that it is safe."""
        assert to_bugs(
            _vuln(affected=[{
                "package": {"ecosystem": "Alpine", "name": "musl"},
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.0"}]}],
            }])
        ) == []

    def test_an_advisory_with_no_bounds_is_skipped(self) -> None:
        """No bound at all would match every version of the package forever."""
        assert to_bugs(
            _vuln(affected=[{"package": {"ecosystem": "PyPI", "name": "django"}}])
        ) == []

    def test_the_evidence_url_points_at_the_advisory(self) -> None:
        assert to_bugs(_vuln())[0].evidence_url == "https://osv.dev/vulnerability/GHSA-xxxx"
