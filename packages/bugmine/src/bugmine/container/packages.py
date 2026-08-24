"""Reading the package databases an image carries.

Three OS formats and several language ones, all of which must be parsed rather than guessed at,
because every failure mode here is the same shape: a database we cannot read yields no packages,
no packages yields no findings, and no findings reads as a clean image.

That is why each parser reports what it *read* as well as what it found. An empty result and an
unreadable file are different answers, and only one of them is good news — the same distinction
the catalog draws between "no findings" and "not covered", applied one layer earlier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bugmine.inventory.models import Dependency

# Where each database lives in a flattened image filesystem.
APK_DB = "lib/apk/db/installed"
DPKG_DB = "var/lib/dpkg/status"
RPM_DIRS = ("var/lib/rpm/", "usr/lib/sysimage/rpm/")


@dataclass(frozen=True)
class ParseResult:
    packages: tuple[Dependency, ...]
    """What was installed."""
    read: tuple[str, ...]
    """Which databases were actually parsed. Empty means we found nothing to read, which is a
    different claim from finding an empty database."""
    unreadable: tuple[str, ...]
    """Databases present but not parseable. Must reach the user: an image whose package list we
    failed to read is unscanned, not clean."""


def _blocks(text: str) -> list[dict[str, str]]:
    """Split an RFC822-ish paragraph file into records.

    Both apk and dpkg use the format: `Key: value` lines, blank line between records. Written
    once because they agree on the framing even though they disagree on every field name.
    """
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: str | None = None
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current, last_key = {}, None
            continue
        if line[0] in " \t" and last_key:
            # A continuation line. Dpkg descriptions span lines and a naive parser reading only
            # `Key: value` treats the continuation as a malformed record and drops the package.
            current[last_key] += "\n" + line.strip()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        last_key = key.strip()
        current[last_key] = value.strip()
    if current:
        records.append(current)
    return records


def parse_apk(text: str) -> tuple[Dependency, ...]:
    """Alpine's installed database.

    Single-letter keys: `P` package, `V` version. Alpine versions carry an `-r<n>` revision
    (`1.2.3-r4`) which is part of the version and not a suffix to strip — `1.2.3-r0` and
    `1.2.3-r4` are different packages to a security advisory.
    """
    out: list[Dependency] = []
    for record in _blocks(text):
        name, version = record.get("P"), record.get("V")
        if name and version:
            out.append(Dependency(ecosystem="apk", name=name, version=version, direct=False))
    return tuple(out)


def parse_dpkg(text: str) -> tuple[Dependency, ...]:
    """Debian and Ubuntu's status file.

    The trap is `Status`. That file lists packages that are *not installed* — removed but with
    config retained, deinstall-pending, half-configured. Treating every record as installed
    reports defects against software that is not on the image.
    """
    out: list[Dependency] = []
    for record in _blocks(text):
        name, version = record.get("Package"), record.get("Version")
        status = record.get("Status", "")
        if not (name and version):
            continue
        # "install ok installed" — the third field is what matters. `deinstall ok config-files`
        # means the binary is gone and only its configuration remains.
        if not status.endswith(" installed"):
            continue
        out.append(Dependency(ecosystem="deb", name=name, version=version, direct=False))
    return tuple(out)


_RPM_NAME = re.compile(
    r"^(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^.]+)$"
)


def parse_rpm_names(names: list[str]) -> tuple[Dependency, ...]:
    """Parse RPM NEVRA strings, e.g. `openssl-libs-3.0.7-27.el9.x86_64`.

    The real RPM database is BerkeleyDB or sqlite depending on distribution age, and reading it
    properly needs librpm. This parses the filename form, which is what `rpm -qa` emits and what
    an sqlite-backed database stores in its `Nvra` index — enough for Phase 1, and the version
    string it produces is the one advisories use.

    Splitting from the right is not optional: package names contain hyphens
    (`openssl-libs`) and so does nothing else in the string, so left-to-right parsing
    attributes half the name to the version.
    """
    out: list[Dependency] = []
    for raw in names:
        match = _RPM_NAME.match(raw.strip())
        if not match:
            continue
        version = f"{match['version']}-{match['release']}"
        out.append(
            Dependency(ecosystem="rpm", name=match["name"], version=version, direct=False)
        )
    return tuple(out)


def parse_python_metadata(text: str) -> Dependency | None:
    """A single `*.dist-info/METADATA` file.

    Case matters going out, not coming in: metadata says `Django`, the index says `django`, and
    the catalog joins on the normalised form.
    """
    name = version = None
    for line in text.splitlines():
        if line.startswith("Name:") and name is None:
            name = line.partition(":")[2].strip()
        elif line.startswith("Version:") and version is None:
            version = line.partition(":")[2].strip()
        elif not line.strip():
            break  # Headers end at the first blank line; the body can contain anything.
    if not (name and version):
        return None
    return Dependency(ecosystem="pypi", name=name, version=version, direct=False)


def parse_npm_package_json(payload: dict) -> Dependency | None:
    name, version = payload.get("name"), payload.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return None
    return Dependency(ecosystem="npm", name=name, version=version, direct=False)
