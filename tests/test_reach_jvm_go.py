"""Reachability for Java, Kotlin and Go.

Same trade as JavaScript, restated because it governs every assertion here: regexes rather than
parsers, and every ambiguity resolves toward "referenced". A missed reference suppresses a real
finding, which is the failure this system must not have; a false positive is dismissible.
"""

from __future__ import annotations

from bugmine.reach.golang import analyse_go_source
from bugmine.reach.jvm import analyse_jvm_source
from bugmine.reach.symbols import affected_symbols


class TestJava:
    def _run(self, files, component="com.fasterxml.jackson.core:jackson-databind",
             title="Remove support for `ObjectMapper`"):  # type: ignore[no-untyped-def]
        return analyse_jvm_source(files, component=component, symbols=affected_symbols(title))

    def test_a_single_type_import_reaches(self) -> None:
        r = self._run({"A.java": """
            import com.fasterxml.jackson.databind.ObjectMapper;
            class A { ObjectMapper m = new ObjectMapper(); }
        """})
        assert r.reached and r.confirmed

    def test_a_wildcard_import_reaches_symbols_never_written(self) -> None:
        """`import x.*` binds every type in the package without naming any. Treating that as
        "no specific symbol" would suppress exactly the findings it should surface — and
        wildcard imports are most of them in older Java."""
        r = self._run({"A.java": """
            import com.fasterxml.jackson.databind.*;
            class A { ObjectMapper m = new ObjectMapper(); }
        """})
        assert r.reached

    def test_an_unimported_dependency_does_not_reach(self) -> None:
        r = self._run({"A.java": "import java.util.List;\nclass A {}\n"})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_imported_but_the_symbol_is_untouched_does_not_reach(self) -> None:
        r = self._run({"A.java": """
            import com.fasterxml.jackson.databind.JsonNode;
            class A { JsonNode n; }
        """})
        assert not r.reached and r.confirmed

    def test_a_static_import_reaches(self) -> None:
        r = self._run(
            {"A.java": "import static org.junit.Assert.assertEquals;\nclass A {}\n"},
            component="junit:junit",
            title="Remove support for `assertEquals`",
        )
        assert r.reached

    def test_an_unrelated_org_does_not_match_on_a_shared_prefix(self) -> None:
        """`org.apache` must not match everything Apache ever published."""
        r = self._run(
            {"A.java": "import org.apache.kafka.clients.Producer;\nclass A { Producer p; }\n"},
            component="org.apache.camel:camel-core",
            title="Remove support for `Producer`",
        )
        assert not r.reached


class TestKotlin:
    def test_an_aliased_import_matches_both_names(self) -> None:
        r = analyse_jvm_source(
            {"A.kt": "import kotlinx.coroutines.flow.Flow as Stream\nval s: Stream? = null\n"},
            component="org.jetbrains.kotlinx:kotlinx-coroutines-core",
            symbols=affected_symbols("Remove support for `Flow`"),
        )
        assert r.reached


class TestGo:
    def _run(self, files, component="github.com/gin-gonic/gin",
             title="Remove support for `New()`"):  # type: ignore[no-untyped-def]
        return analyse_go_source(files, component=component, symbols=affected_symbols(title))

    def test_a_single_import_and_qualified_use_reaches(self) -> None:
        r = self._run({"m.go": 'import "github.com/gin-gonic/gin"\n\nfunc f() { gin.New() }\n'})
        assert r.reached and r.confirmed

    def test_a_grouped_import_reaches(self) -> None:
        r = self._run({"m.go": '''
import (
    "fmt"
    "github.com/gin-gonic/gin"
)

func f() { gin.New() }
'''})
        assert r.reached

    def test_an_aliased_import_reaches(self) -> None:
        r = self._run({"m.go": '''
import (
    g "github.com/gin-gonic/gin"
)

func f() { g.New() }
'''})
        assert r.reached

    def test_a_blank_import_still_reaches_the_package(self) -> None:
        """`_ "pkg"` binds nothing but runs the package's init, which is how some defects
        manifest. Treating it as unused would suppress those."""
        r = self._run(
            {"m.go": 'import _ "github.com/gin-gonic/gin"\n'},
            title="Remove support for `gin`",
        )
        assert r.reached

    def test_a_submodule_belongs_to_its_module(self) -> None:
        r = self._run({"m.go": '''
import "github.com/gin-gonic/gin/binding"

func f() { binding.New() }
'''})
        assert r.reached

    def test_an_unimported_module_does_not_reach(self) -> None:
        r = self._run({"m.go": 'import "net/http"\n\nfunc f() { http.Get("x") }\n'})
        assert not r.reached and r.confirmed
        assert r.suppressible

    def test_a_record_naming_no_symbol_is_unknown(self) -> None:
        r = self._run(
            {"m.go": 'import "github.com/gin-gonic/gin"\n'},
            title="Drop support for Go 1.19",
        )
        assert r.reached and not r.confirmed
        assert not r.suppressible
