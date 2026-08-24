"""Package database parsing.

Every failure in this file has the same shape: a database we misread yields fewer packages,
fewer packages yields fewer findings, and fewer findings reads as a cleaner image. Nothing here
fails loudly on its own, so the tests have to.
"""

from __future__ import annotations

from bugmine.container import packages


class TestAlpine:
    def test_packages_are_read(self) -> None:
        db = "P:musl\nV:1.2.4-r2\nA:x86_64\n\nP:busybox\nV:1.36.1-r5\nA:x86_64\n"
        got = packages.parse_apk(db)
        assert [(d.name, d.version) for d in got] == [
            ("musl", "1.2.4-r2"),
            ("busybox", "1.36.1-r5"),
        ]

    def test_the_alpine_revision_is_part_of_the_version(self) -> None:
        """`1.2.3-r0` and `1.2.3-r4` are different packages to an advisory. Stripping `-r4` as
        a build suffix would match a fixed package against a vulnerable range."""
        got = packages.parse_apk("P:openssl\nV:3.1.4-r5\n")
        assert got[0].version == "3.1.4-r5"

    def test_a_record_missing_a_version_is_skipped(self) -> None:
        assert packages.parse_apk("P:broken\nA:x86_64\n") == ()


class TestDebian:
    def test_installed_packages_are_read(self) -> None:
        db = (
            "Package: libc6\nStatus: install ok installed\nVersion: 2.36-9+deb12u4\n\n"
            "Package: zlib1g\nStatus: install ok installed\nVersion: 1:1.2.13.dfsg-1\n"
        )
        got = packages.parse_dpkg(db)
        assert [(d.name, d.version) for d in got] == [
            ("libc6", "2.36-9+deb12u4"),
            ("zlib1g", "1:1.2.13.dfsg-1"),
        ]

    def test_removed_packages_are_not_reported_as_present(self) -> None:
        """The status file lists packages that are *not* installed. Reporting on
        `deinstall ok config-files` files defects against software that is not on the image —
        a false positive the user cannot act on, because there is nothing there to upgrade.
        """
        db = (
            "Package: gone\nStatus: deinstall ok config-files\nVersion: 1.0\n\n"
            "Package: here\nStatus: install ok installed\nVersion: 2.0\n"
        )
        assert [d.name for d in packages.parse_dpkg(db)] == ["here"]

    def test_a_half_configured_package_is_not_counted_as_installed(self) -> None:
        db = "Package: broken\nStatus: install ok half-configured\nVersion: 1.0\n"
        assert packages.parse_dpkg(db) == ()

    def test_a_multiline_description_does_not_swallow_the_next_package(self) -> None:
        """Dpkg descriptions span lines. A parser reading only `Key: value` treats the
        continuation as a new malformed record and loses whatever followed it."""
        db = (
            "Package: first\nStatus: install ok installed\nVersion: 1.0\n"
            "Description: a thing\n it does stuff\n .\n across several lines\n\n"
            "Package: second\nStatus: install ok installed\nVersion: 2.0\n"
        )
        assert [d.name for d in packages.parse_dpkg(db)] == ["first", "second"]

    def test_an_epoch_is_kept(self) -> None:
        """`1:1.2.13` is older than `2:1.0` despite the larger upstream version. Dropping the
        epoch inverts that comparison."""
        got = packages.parse_dpkg(
            "Package: p\nStatus: install ok installed\nVersion: 1:1.2.13.dfsg-1\n"
        )
        assert got[0].version.startswith("1:")


class TestRpm:
    def test_a_nevra_is_split_from_the_right(self) -> None:
        """Package names contain hyphens. Left-to-right parsing gives `openssl` a version of
        `libs`, which matches nothing and reports the package as absent."""
        got = packages.parse_rpm_names(["openssl-libs-3.0.7-27.el9.x86_64"])
        assert (got[0].name, got[0].version) == ("openssl-libs", "3.0.7-27.el9")

    def test_a_simple_name_parses(self) -> None:
        got = packages.parse_rpm_names(["bash-5.1.8-6.el9.x86_64"])
        assert (got[0].name, got[0].version) == ("bash", "5.1.8-6.el9")

    def test_a_name_with_several_hyphens(self) -> None:
        got = packages.parse_rpm_names(["python3-pip-wheel-21.2.3-8.el9.noarch"])
        assert got[0].name == "python3-pip-wheel"

    def test_unparseable_entries_are_dropped_not_guessed(self) -> None:
        assert packages.parse_rpm_names(["nonsense", ""]) == ()


class TestLanguagePackages:
    def test_python_metadata(self) -> None:
        got = packages.parse_python_metadata(
            "Metadata-Version: 2.1\nName: Django\nVersion: 4.2.11\nSummary: x\n"
        )
        assert got is not None
        assert (got.name, got.version, got.ecosystem) == ("Django", "4.2.11", "pypi")

    def test_python_metadata_stops_at_the_body(self) -> None:
        """The body of a METADATA file is the README, which can contain anything at all —
        including lines that look exactly like headers."""
        got = packages.parse_python_metadata(
            "Name: real\nVersion: 1.0\n\nName: not-a-package\nVersion: 9.9\n"
        )
        assert got is not None and got.name == "real" and got.version == "1.0"

    def test_incomplete_metadata_yields_nothing(self) -> None:
        assert packages.parse_python_metadata("Name: nameonly\n") is None

    def test_npm_package_json(self) -> None:
        got = packages.parse_npm_package_json({"name": "lodash", "version": "4.17.21"})
        assert got is not None and got.ecosystem == "npm"

    def test_npm_package_json_without_a_version(self) -> None:
        """A package.json in an application directory often has no version. It is not a
        dependency and must not be reported as one at an invented version."""
        assert packages.parse_npm_package_json({"name": "my-app"}) is None
