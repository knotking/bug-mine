"""Layer flattening.

The property under test throughout: the result is the filesystem the *container* sees, not the
union of what every layer ever contained. Getting this wrong reports packages that are not in
the image, which the user cannot act on because there is nothing there to upgrade.
"""

from __future__ import annotations

import io
import tarfile

from bugmine.container.layers import flatten, layer_entries


def _layer(files: dict[str, bytes], dirs: tuple[str, ...] = ()):
    entries = [(d, None, True) for d in dirs]
    entries += [(p, (lambda b=b: b), False) for p, b in files.items()]
    return entries


def _tar(files: dict[str, bytes]) -> tarfile.TarFile:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


class TestLayering:
    def test_a_later_layer_replaces_an_earlier_file(self) -> None:
        img = flatten([_layer({"etc/conf": b"old"}), _layer({"etc/conf": b"new"})])
        assert img.files["etc/conf"] == b"new"

    def test_paths_normalise_across_builders(self) -> None:
        """Builders emit `./etc/conf`, `/etc/conf` and `etc/conf`. Unnormalised these are three
        keys, so a later layer fails to overwrite an earlier one and both survive."""
        img = flatten([_layer({"./etc/conf": b"old"}), _layer({"etc/conf": b"new"})])
        assert list(img.files) == ["etc/conf"]
        assert img.files["etc/conf"] == b"new"


class TestWhiteouts:
    def test_a_deleted_file_is_absent_from_the_result(self) -> None:
        """The case the whole design turns on: a package installed in an early layer and
        removed in a later one is not in the running image."""
        img = flatten([_layer({"usr/bin/curl": b"x"}), _layer({"usr/bin/.wh.curl": b""})])
        assert "usr/bin/curl" not in img.files
        assert "usr/bin/curl" in img.deleted

    def test_deleting_a_directory_removes_its_contents(self) -> None:
        img = flatten(
            [
                _layer({"opt/tool/bin/a": b"x", "opt/tool/lib/b": b"y"}),
                _layer({"opt/.wh.tool": b""}),
            ]
        )
        assert not [p for p in img.files if p.startswith("opt/tool")]

    def test_an_opaque_marker_clears_inherited_contents(self) -> None:
        """The convention people forget. It appears rarely, and missing it leaves an entire
        deleted tree visible in the result."""
        img = flatten(
            [
                _layer({"var/cache/old-a": b"x", "var/cache/old-b": b"y"}),
                _layer({"var/cache/.wh..wh..opq": b"", "var/cache/new": b"z"}),
            ]
        )
        assert "var/cache/old-a" not in img.files
        assert "var/cache/old-b" not in img.files
        assert img.files["var/cache/new"] == b"z", "the layer's own additions must survive"

    def test_a_whiteout_and_replacement_in_one_layer_is_a_replacement(self) -> None:
        """Layers whiteout a path and immediately write a new file there. Applying additions
        before deletions would delete the replacement and report the file as removed."""
        img = flatten(
            [
                _layer({"usr/bin/tool": b"v1"}),
                _layer({"usr/bin/.wh.tool": b"", "usr/bin/tool": b"v2"}),
            ]
        )
        assert img.files["usr/bin/tool"] == b"v2"
        assert "usr/bin/tool" not in img.deleted

    def test_a_file_restored_by_a_later_layer_is_present(self) -> None:
        img = flatten(
            [
                _layer({"a": b"1"}),
                _layer({".wh.a": b""}),
                _layer({"a": b"2"}),
            ]
        )
        assert img.files["a"] == b"2" and "a" not in img.deleted


class TestSafety:
    def test_traversal_paths_are_dropped(self) -> None:
        img = flatten([_layer({"../../etc/passwd": b"x", "ok": b"y"})])
        assert list(img.files) == ["ok"]

    def test_an_oversized_member_is_skipped_and_flagged(self) -> None:
        """Skipped, and the result says so. A silently truncated read looks exactly like a
        small image."""
        from bugmine.container.layers import MAX_MEMBER_BYTES

        img = flatten([_layer({"big": b"x" * (MAX_MEMBER_BYTES + 1), "small": b"y"})])
        assert "big" not in img.files
        assert img.files["small"] == b"y"
        assert img.truncated

    def test_a_complete_read_is_not_flagged_as_truncated(self) -> None:
        assert not flatten([_layer({"a": b"x"})]).truncated


class TestSelectiveReading:
    def test_only_wanted_files_are_read(self) -> None:
        """A real image is gigabytes. Reading all of it to find a 40KB package database is the
        difference between a scan that is affordable and one that is not."""
        tar = _tar({"var/lib/dpkg/status": b"db", "app/huge.bin": b"z" * 1000})
        entries = layer_entries(tar, lambda p: p.endswith("dpkg/status"))
        readable = [p for p, r, _ in entries if r is not None]
        assert readable == ["var/lib/dpkg/status"]

    def test_whiteouts_are_kept_even_when_unwanted(self) -> None:
        """Skipping a deletion marker because the deleted file was uninteresting leaves the
        deletion unrecorded — which reinstates the union-of-layers error the predicate was
        meant to be orthogonal to."""
        tar = _tar({"usr/bin/.wh.curl": b"", "app/thing": b"x"})
        names = [p for p, _, _ in layer_entries(tar, lambda p: False)]
        assert "usr/bin/.wh.curl" in names

    def test_a_selectively_read_layer_still_flattens(self) -> None:
        tar_a = _tar({"var/lib/dpkg/status": b"one", "app/x": b"noise"})
        tar_b = _tar({"var/lib/dpkg/.wh.status": b""})
        img = flatten(
            [
                layer_entries(tar_a, lambda p: p.endswith("dpkg/status")),
                layer_entries(tar_b, lambda p: p.endswith("dpkg/status")),
            ]
        )
        assert "var/lib/dpkg/status" not in img.files
