"""Reading the hidden fields out of Forgejo's two browser-only forms."""

from __future__ import annotations

from bootstrap.htmlform import hidden_inputs

CONSENT_PAGE = """
<form method="post" action="/login/oauth/grant">
  <input type="hidden" name="_csrf" value="abc.def">
  <input type="hidden" name="client_id" value="1234-5678">
  <input type="hidden" name="redirect_uri" value="http://localhost:8000/authorize">
  <input type="hidden" name="scope" value="">
  <input type="text" name="typed_by_a_person" value="not hidden">
  <input type="hidden" value="no name at all">
  <button type="submit" name="granted" value="true">Authorize</button>
</form>
"""


def test_collects_only_hidden_fields() -> None:
    fields = hidden_inputs(CONSENT_PAGE)

    assert fields == {
        "_csrf": "abc.def",
        "client_id": "1234-5678",
        "redirect_uri": "http://localhost:8000/authorize",
        "scope": "",
    }


def test_a_hidden_field_with_no_value_becomes_an_empty_string() -> None:
    assert hidden_inputs('<input type="hidden" name="state">') == {"state": ""}


def test_a_page_with_no_form_has_no_fields() -> None:
    assert hidden_inputs("<p>Signed in</p>") == {}
