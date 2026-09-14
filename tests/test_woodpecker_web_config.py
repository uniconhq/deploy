"""Pulling the CSRF token out of Woodpecker's /web-config.js.

The file is JavaScript, not JSON, and Woodpecker hands its own single-page app
the CSRF token there. Real output, trimmed, from woodpecker-server v3.18.1.
"""

from __future__ import annotations

from bootstrap.woodpecker import _javascript_string

WEB_CONFIG = """window.WOODPECKER_USER = null;
window.WOODPECKER_CSRF = "gkeSA0Vg.thelongtokengoeshere";
window.WOODPECKER_VERSION = "3.18.1";
window.WOODPECKER_ENABLE_SWAGGER = true;
"""


def test_reads_the_value_of_the_named_variable() -> None:
    assert (
        _javascript_string(WEB_CONFIG, "WOODPECKER_CSRF")
        == "gkeSA0Vg.thelongtokengoeshere"
    )


def test_a_later_variable_does_not_shadow_an_earlier_one() -> None:
    assert _javascript_string(WEB_CONFIG, "WOODPECKER_VERSION") == "3.18.1"


def test_an_empty_value_is_read_as_empty() -> None:
    """Woodpecker serves an empty CSRF token to anyone without a session, which
    is what the token dance sees if its sign-in silently failed."""
    assert _javascript_string('window.WOODPECKER_CSRF = "";', "WOODPECKER_CSRF") == ""


def test_a_name_that_is_not_there_is_none() -> None:
    assert _javascript_string(WEB_CONFIG, "WOODPECKER_ABSENT") is None


def test_a_name_with_no_quoted_value_is_none() -> None:
    unquoted = "window.WOODPECKER_CSRF = null;"

    assert _javascript_string(unquoted, "WOODPECKER_CSRF") is None
