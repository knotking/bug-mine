"""Inventorying a pulled image.

The property that distinguishes this from every CVE scanner: the inventory is taken from the
*flattened* filesystem, so a package installed in an early layer and removed in a later one is
already gone before matching happens. That is a narrowing the user does not have to trust,
because the package genuinely is not in the image.
"""

from __future__ import annotations

import io
import tarfile

from bugmine.container import inventory
from bugmine.container.layers import flatten, layer_entries


def _tar(files: dict[str, bytes]) -> tarfile.TarFile:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


def _inventory(*layers: dict[str, bytes]) -> inventory.ImageInventory:
    return inventory.read(
        flatten(layer_entries(_tar(files), inventory.wanted) for files in layers)
    )


DPKG = b"Package: libssl3\nStatus: install ok installed\nVersion: 3.0.11-1\n"
APK = b"P:musl\nV:1.2.4-r2\n"


class TestMultipleEcosystems:
    def test_os_and_language_packages_are_inventoried_together(self) -> None:
        """A repository scan sees one ecosystem. An image carries several at once."""
        inv = _inventory(
            {
                "var/lib/dpkg/status": DPKG,
                "usr/lib/python3/site-packages/django-4.2.11.dist-info/METADATA":
                    b"Name: Django\nVersion: 4.2.11\n",
                "app/node_modules/lodash/package.json":
                    b'{"name":"lodash","version":"4.17.21"}',
            }
        )
        assert {(d.ecosystem, d.name) for d in inv.dependencies} == {
            ("deb", "libssl3"),
            ("pypi", "Django"),
            ("npm", "lodash"),
        }

    def test_the_same_name_in_two_ecosystems_stays_distinct(self) -> None:
        """`openssl` the Debian package and `openssl` the wheel are different components with
        different advisories. Collapsing them matches the wrong records against the wrong
        software."""
        inv = _inventory(
            {
                "var/lib/dpkg/status":
                    b"Package: openssl\nStatus: install ok installed\nVersion: 3.0.11-1\n",
                "site-packages/openssl-1.0.0.dist-info/METADATA":
                    b"Name: openssl\nVersion: 1.0.0\n",
            }
        )
        assert len(inv.dependencies) == 2
        assert {d.ecosystem for d in inv.dependencies} == {"deb", "pypi"}


class TestLayerDeletionNarrowsBeforeMatching:
    def test_a_package_database_deleted_later_is_not_inventoried(self) -> None:
        inv = _inventory({"var/lib/dpkg/status": DPKG}, {"var/lib/dpkg/.wh.status": b""})
        assert inv.dependencies == ()
        assert inv.sources == ()

    def test_a_wheel_removed_in_a_later_layer_is_gone(self) -> None:
        """The multi-stage build case, and the reason this matters in practice: a builder layer
        installs a toolchain, a later layer deletes it, and every union-based scanner reports
        on software that is not in the shipped image."""
        inv = _inventory(
            {"site-packages/evil-1.0.dist-info/METADATA": b"Name: evil\nVersion: 1.0\n",
             "site-packages/keep-2.0.dist-info/METADATA": b"Name: keep\nVersion: 2.0\n"},
            {"site-packages/.wh.evil-1.0.dist-info": b""},
        )
        assert [d.name for d in inv.dependencies] == ["keep"]

    def test_a_replaced_database_reports_the_new_contents(self) -> None:
        inv = _inventory(
            {"lib/apk/db/installed": b"P:old\nV:1.0\n"},
            {"lib/apk/db/installed": b"P:new\nV:2.0\n"},
        )
        assert [d.name for d in inv.dependencies] == ["new"]

    def test_deletions_are_counted_as_evidence(self) -> None:
        """A suppression with no reason is indistinguishable from a gap in coverage."""
        inv = _inventory({"site-packages/a-1.0.dist-info/METADATA": b"Name: a\nVersion: 1.0\n"},
                         {"site-packages/.wh.a-1.0.dist-info": b""})
        assert inv.deleted_paths >= 1


class TestSelectiveReading:
    def test_application_code_is_not_read(self) -> None:
        """The predicate is why a container scan is affordable. A real image is gigabytes and
        the databases in it are megabytes."""
        assert not inventory.wanted("app/src/main.py")
        assert not inventory.wanted("usr/bin/python3")
        assert inventory.wanted("var/lib/dpkg/status")

    def test_an_applications_own_package_json_is_not_a_dependency(self) -> None:
        """Counting it files findings against the thing being scanned."""
        assert not inventory.wanted("app/package.json")
        assert inventory.wanted("app/node_modules/lodash/package.json")

    def test_rpm_directories_are_wanted(self) -> None:
        assert inventory.wanted("var/lib/rpm/Packages")
        assert inventory.wanted("usr/lib/sysimage/rpm/rpmdb.sqlite")


class TestHonestyAboutGaps:
    def test_an_image_with_no_recognised_database_reports_no_sources(self) -> None:
        """Zero packages and zero sources is "we could not read this", not "it is clean". The
        distinction has to survive to the user."""
        inv = _inventory({"some/random/file": b"x"})
        assert inv.dependencies == ()
        assert inv.sources == ()
        assert not inv.complete

    def test_a_successful_read_is_complete(self) -> None:
        inv = _inventory({"var/lib/dpkg/status": DPKG})
        assert inv.complete

    def test_a_truncated_read_is_not_complete(self) -> None:
        """A partial inventory looks exactly like a small image and reports just as cleanly."""
        from bugmine.container.layers import MAX_MEMBER_BYTES

        inv = _inventory(
            {"var/lib/dpkg/status": DPKG, "lib/apk/db/installed": b"x" * (MAX_MEMBER_BYTES + 1)}
        )
        assert inv.truncated
        assert not inv.complete

    def test_an_undecodable_database_does_not_crash_the_scan(self) -> None:
        inv = _inventory({"var/lib/dpkg/status": b"\xff\xfe\x00binary"})
        assert isinstance(inv.dependencies, tuple)


class TestDeduplication:
    def test_the_same_wheel_in_two_paths_is_reported_once(self) -> None:
        """Reporting it twice doubles every finding against it, which reads as two problems."""
        meta = b"Name: shared\nVersion: 1.0\n"
        inv = _inventory(
            {
                "usr/lib/python3.11/site-packages/shared-1.0.dist-info/METADATA": meta,
                "usr/local/lib/python3.11/site-packages/shared-1.0.dist-info/METADATA": meta,
            }
        )
        assert [d.name for d in inv.dependencies] == ["shared"]

    def test_two_versions_of_one_package_are_both_kept(self) -> None:
        """Different versions are genuinely different findings — one may be patched."""
        inv = _inventory(
            {
                "a/site-packages/dup-1.0.dist-info/METADATA": b"Name: dup\nVersion: 1.0\n",
                "b/site-packages/dup-2.0.dist-info/METADATA": b"Name: dup\nVersion: 2.0\n",
            }
        )
        assert len(inv.dependencies) == 2


class TestImageMetadata:
    def _image(self, config: dict) -> object:
        from bugmine.container.reference import parse
        from bugmine.container.registry import PulledImage

        return PulledImage(reference=parse("x/y:1"), digest="sha256:" + "a" * 64, config=config)

    def test_the_path_is_read_from_the_environment(self) -> None:
        img = self._image({"config": {"Env": ["PATH=/usr/local/bin:/usr/bin", "TZ=UTC"]}})
        assert img.path_dirs == ("usr/local/bin", "usr/bin")  # type: ignore[attr-defined]

    def test_a_missing_path_falls_back_to_the_conventional_one(self) -> None:
        assert self._image({}).path_dirs == ("usr/local/bin", "usr/bin", "bin")  # type: ignore[attr-defined]

    def test_entrypoint_and_cmd_combine(self) -> None:
        img = self._image({"config": {"Entrypoint": ["/bin/sh", "-c"], "Cmd": ["app"]}})
        assert img.entrypoint == ("/bin/sh", "-c", "app")  # type: ignore[attr-defined]

    def test_an_unlabelled_base_image_is_absent_not_guessed(self) -> None:
        """A label is not a guarantee and plenty of builders write nothing. Inferring one from
        layer history would be a guess wearing the same field name as a fact."""
        assert self._image({"config": {}}).base_image_hint is None  # type: ignore[attr-defined]

    def test_a_labelled_base_image_is_reported(self) -> None:
        img = self._image(
            {"config": {"Labels": {"org.opencontainers.image.base.name": "python:3.12"}}}
        )
        assert img.base_image_hint == "python:3.12"  # type: ignore[attr-defined]


class TestEveryEcosystemHasADomain:
    """The map that already caused one silent failure.

    An ecosystem missing from `ECOSYSTEM_DOMAIN` makes every dependency in it report "not
    covered" even when the catalog holds records, because the lookup never happens. That hid
    the entire Java, Go, Android and iOS catalog behind an answer reading as "we checked and
    found nothing". Adding three container ecosystems is exactly the same opportunity.
    """

    def test_every_ecosystem_the_container_inventory_emits_is_mapped(self) -> None:
        from bugmine.scan import ECOSYSTEM_DOMAIN

        emitted = {"apk", "deb", "rpm", "pypi", "npm"}
        missing = sorted(emitted - set(ECOSYSTEM_DOMAIN))
        assert not missing, f"container ecosystems with no subject domain: {missing}"

    def test_os_packages_map_to_the_operating_system_domain(self) -> None:
        """Not repo_library. An apk package is what the OS ships, and its records live under
        the domain the catalog files them in — mapping it wrong looks up the wrong shelf."""
        from bugmine.models import SubjectDomain
        from bugmine.scan import ECOSYSTEM_DOMAIN

        for eco in ("apk", "deb", "rpm"):
            assert ECOSYSTEM_DOMAIN[eco] is SubjectDomain.OPERATING_SYSTEM

    def test_reachability_does_not_claim_to_support_os_packages(self) -> None:
        """An OS package's call graph is not determinable from an image without source. Listing
        it as supported would let findings be suppressed on an analysis that never ran."""
        from bugmine.scan import REACHABILITY_SUPPORTED

        assert not ({"apk", "deb", "rpm"} & REACHABILITY_SUPPORTED)
