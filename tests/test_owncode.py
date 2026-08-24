"""Own-code analysis: defects in the user's code, not their dependencies.

FR-37 shapes everything. A catalog-grounded finding cites a record — this component, this
version, this documented defect. An own-code finding has nothing to cite; it is the system's own
judgment about code it read once. Presented in one undifferentiated list, the weaker evidence
borrows the authority of the stronger, and the citation mechanism that makes the catalog
trustworthy becomes the thing that launders a guess.
"""

from __future__ import annotations

from bugmine.owncode import analyse_python


def _rules(source: str) -> set[str]:
    return {f.rule for f in analyse_python({"a.py": source}).findings}


class TestEvidenceIsNeverBorrowed:
    def test_an_own_code_finding_has_no_citations(self) -> None:
        """Empty by construction rather than by convention: there is no field to put one in, so
        no code path can attach a citation and borrow the catalog's authority."""
        result = analyse_python({"a.py": "try:\n    x()\nexcept:\n    pass\n"})
        assert result.findings
        assert all(f.citations == () for f in result.findings)

    def test_every_finding_names_the_rule_it_matched(self) -> None:
        """What an own-code finding has instead of a citation: a rule a reader can judge, rather
        than a verdict they have to trust."""
        result = analyse_python({"a.py": "def f(items=[]):\n    pass\n"})
        assert all(f.rule for f in result.findings)


class TestRules:
    def test_bare_except(self) -> None:
        assert "bare-except" in _rules("try:\n    x()\nexcept:\n    pass\n")

    def test_swallowed_exception(self) -> None:
        """The pattern this project itself shipped: workers converted exceptions into a generic
        error and the only useful detail was gone by the time anyone looked."""
        assert "swallowed-exception" in _rules("try:\n    x()\nexcept ValueError:\n    pass\n")

    def test_mutable_default(self) -> None:
        assert "mutable-default" in _rules("def f(items=[]):\n    return items\n")

    def test_a_keyword_only_mutable_default_is_caught(self) -> None:
        assert "mutable-default" in _rules("def f(*, items={}):\n    return items\n")

    def test_raise_without_from(self) -> None:
        source = "try:\n    x()\nexcept ValueError:\n    raise RuntimeError('no')\n"
        assert "raise-without-from" in _rules(source)

    def test_a_correct_reraise_is_not_flagged(self) -> None:
        source = "try:\n    x()\nexcept ValueError as e:\n    raise RuntimeError('no') from e\n"
        assert "raise-without-from" not in _rules(source)

    def test_a_bare_reraise_is_not_flagged(self) -> None:
        """`raise` alone preserves everything; flagging it would train people to ignore the rule."""
        assert "raise-without-from" not in _rules("try:\n    x()\nexcept ValueError:\n    raise\n")

    def test_clean_code_produces_nothing(self) -> None:
        source = (
            "def f(items=None):\n"
            "    items = items or []\n"
            "    try:\n"
            "        return items[0]\n"
            "    except IndexError as e:\n"
            "        raise ValueError('empty') from e\n"
        )
        assert analyse_python({"a.py": source}).findings == []


class TestGapsAreCounted:
    def test_an_unparseable_file_is_counted_not_ignored(self) -> None:
        """Zero findings over unread files is not the same answer as zero over read ones."""
        result = analyse_python({"bad.py": "def broken(:\n", "ok.py": "x = 1\n"})
        assert result.files_unparseable == 1
        assert result.files_analysed == 1
