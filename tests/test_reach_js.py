"""JavaScript reachability.

Regex-based, deliberately, because a real parser means shipping a Node runtime or a wasm blob
into a worker whose security value is having no egress and few moving parts. The trade is stated
in the module and enforced here: every ambiguity resolves toward "referenced", since a missed
reference suppresses a real finding and a false positive is merely dismissible.
"""

from __future__ import annotations

from bugmine.reach.javascript import analyse_javascript_source
from bugmine.reach.symbols import affected_symbols

TITLE = "Remove support for `debounce()`"


def _run(files: dict[str, str], *, component: str = "lodash", title: str = TITLE):
    return analyse_javascript_source(files, component=component, symbols=affected_symbols(title))


class TestImportShapes:
    def test_named_import(self) -> None:
        r = _run({"a.js": "import {debounce} from 'lodash';\ndebounce(fn, 100);\n"})
        assert r.reached and r.confirmed

    def test_aliased_named_import_matches_both_names(self) -> None:
        """A record naming `debounce` must match code that renamed it — and code using the new
        name must match a record naming the original. Keeping one side would miss a reference."""
        r = _run({"a.js": "import {debounce as d} from 'lodash';\nd(fn, 100);\n"})
        assert r.reached and r.confirmed

    def test_default_import_with_member_access(self) -> None:
        r = _run({"a.js": "import _ from 'lodash';\n_.debounce(fn, 100);\n"})
        assert r.reached and r.confirmed

    def test_namespace_import(self) -> None:
        r = _run({"a.js": "import * as ns from 'lodash';\nns.debounce(fn);\n"})
        assert r.reached and r.confirmed

    def test_commonjs_destructuring(self) -> None:
        r = _run({"a.js": "const {debounce} = require('lodash');\ndebounce(fn);\n"})
        assert r.reached and r.confirmed

    def test_commonjs_default(self) -> None:
        r = _run({"a.js": "const _ = require('lodash');\n_.debounce(fn);\n"})
        assert r.reached and r.confirmed

    def test_dynamic_import_of_a_subpath(self) -> None:
        """`lodash/debounce` is lodash, and the subpath itself names the symbol."""
        r = _run({"a.js": "const d = await import('lodash/debounce');\n"})
        assert r.reached

    def test_scoped_package_subpath(self) -> None:
        r = _run(
            {"a.js": "import {render} from '@scope/pkg/client';\nrender();\n"},
            component="@scope/pkg",
            title="Remove support for `render()`",
        )
        assert r.reached and r.confirmed


class TestNarrowing:
    def test_a_package_never_imported_does_not_reach(self) -> None:
        r = _run({"a.js": "import React from 'react';\n"})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_imported_but_the_symbol_is_untouched_does_not_reach(self) -> None:
        """The case the feature exists for: the dependency is present, the defect is not."""
        r = _run({"a.js": "import {throttle} from 'lodash';\nthrottle(fn, 100);\n"})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_a_relative_import_is_the_projects_own_module(self) -> None:
        r = _run({"a.js": "import {debounce} from './lodash';\ndebounce(fn);\n"})
        assert not r.reached and r.confirmed


class TestUncertaintyIsNeverSilence:
    def test_a_record_naming_no_symbol_is_unknown(self) -> None:
        r = _run({"a.js": "import _ from 'lodash';\n"}, title="Drop support for Node 14")
        assert r.reached and not r.confirmed
        assert not r.suppressible

    def test_a_side_effect_import_still_reaches_the_package(self) -> None:
        """No binding, but a removed module breaks at load time."""
        r = _run(
            {"a.js": "import 'lodash/debounce';\n"},
            title="Remove support for `debounce`",
        )
        assert r.reached
