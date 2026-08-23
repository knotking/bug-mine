"""Static reachability narrowing.

The asymmetry is the whole design. Reporting a defect the user never hits costs them attention;
suppressing one they do hit costs them the incident BugMine existed to prevent. So every test
here checks not just the answer but whether it is allowed to *hide* a finding — `suppressible`
is true only for a positive determination that the code is untouched.
"""

from __future__ import annotations

from bugmine.reach import affected_symbols, analyse_python_source

REMOVED = "Remove support for `eval_type_backport()`"


def _run(files: dict[str, str], *, component: str = "pydantic", title: str = REMOVED):
    return analyse_python_source(files, component=component, symbols=affected_symbols(title))


class TestNarrowing:
    def test_a_dependency_never_imported_does_not_reach(self) -> None:
        r = _run({"a.py": "import requests\nrequests.get('https://x.test')\n"})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_imported_but_the_symbol_is_untouched_does_not_reach(self) -> None:
        """The case the whole feature exists for: the dependency is present, the defect is not."""
        r = _run({"a.py": "from pydantic import BaseModel\n\n\nclass M(BaseModel):\n    pass\n"})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_using_the_symbol_reaches_with_evidence(self) -> None:
        r = _run({"a.py": "from pydantic import eval_type_backport\neval_type_backport()\n"})
        assert r.reached and r.confirmed
        assert not r.suppressible
        assert r.evidence[0].path == "a.py"
        assert r.evidence[0].symbol == "pydantic.eval_type_backport"


class TestAliasing:
    """Resolving local names back to imports is the entire job; missing one suppresses a real
    finding, which is the failure this must not have."""

    def test_an_aliased_symbol_import_still_reaches(self) -> None:
        r = _run({"a.py": "from pydantic import eval_type_backport as etb\netb()\n"})
        assert r.reached and r.confirmed

    def test_an_aliased_module_still_reaches(self) -> None:
        r = _run({"a.py": "import pydantic as pd\npd.eval_type_backport()\n"})
        assert r.reached and r.confirmed

    def test_a_submodule_alias_resolves_through_the_chain(self) -> None:
        r = analyse_python_source(
            {"a.py": "import sqlalchemy.orm as orm\norm.Session.flush(objects=1)\n"},
            component="sqlalchemy",
            symbols=affected_symbols("The `_orm.Session.flush.objects` parameter is deprecated"),
        )
        assert r.reached and r.confirmed
        assert r.evidence[0].symbol == "sqlalchemy.orm.Session.flush"

    def test_a_shadowing_local_name_is_not_a_reference(self) -> None:
        """`eval_type_backport` defined locally is not the dependency's."""
        r = _run({"a.py": "import pydantic\n\n\ndef eval_type_backport():\n    return 1\n"})
        assert not r.reached and r.confirmed


class TestUncertaintyIsNeverSilence:
    def test_a_record_naming_no_symbol_is_unknown_not_absent(self) -> None:
        """"Drop support for Python 3.9" names no symbol. That is a limit of the record, and
        answering "not reached" would turn our gap into the user's false assurance."""
        r = _run({"a.py": "import pydantic\n"}, title="Drop support for Python 3.9")
        assert r.reached and not r.confirmed
        assert not r.suppressible

    def test_unparseable_source_is_unknown_not_absent(self) -> None:
        r = _run({"a.py": "def broken(:\n"})
        assert not r.confirmed
        assert not r.suppressible

    def test_an_unparseable_file_alongside_a_real_use_still_reaches(self) -> None:
        r = _run({
            "bad.py": "def broken(:\n",
            "ok.py": "from pydantic import eval_type_backport\neval_type_backport()\n",
        })
        assert r.reached and r.confirmed

    def test_an_unparseable_file_downgrades_an_otherwise_clean_negative(self) -> None:
        """Imported, symbol not seen — but a file could not be read, so "not used" is not
        something we actually know."""
        r = _run({"bad.py": "def broken(:\n", "ok.py": "import pydantic\nprint(pydantic)\n"})
        assert not r.confirmed
        assert not r.suppressible


class TestSymbolExtraction:
    def test_prose_yields_no_symbols(self) -> None:
        assert affected_symbols("Fixed issue where the behaviour changed") == frozenset()

    def test_a_documentation_prefix_is_stripped(self) -> None:
        """SQLAlchemy writes `_orm.Session`, where `_orm` is a docs namespace, not an import."""
        assert "Session.flush" in affected_symbols("`_orm.Session.flush` is deprecated")

    def test_a_parent_is_implied_but_never_reduced_to_a_bare_name(self) -> None:
        """A record about a parameter must match the call it belongs to. It must not reduce to
        `Session`, which would match every use of the class and abandon the precision."""
        symbols = affected_symbols("`_orm.Session.flush.objects` is deprecated")
        assert "Session.flush" in symbols
        assert "Session" not in symbols
