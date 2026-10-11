"""The forge's own pages that people meet on the way into the app, on the
running compose stack, from the browser.

A person made with the operator's command signs in for the first time,
which takes them through Forgejo's sign-in, the change of the first
password and the page asking them to approve Unicon. "Back to Unicon" on
that page leads to the app's home: they are signed in at the forge by then
but not yet in the app, so the app's Account page would send them through
sign-in and back to the same page.

It runs against a stack that is already up with the `app` profile, and is
skipped unless `UNICON_E2E_URL` names the app, for example
`http://localhost:8080`.
"""

from __future__ import annotations

import re
import secrets

from playwright.sync_api import Browser, expect

from tests.e2e.stack import APP, create_account, needs_stack


@needs_stack
def test_back_to_unicon_on_the_consent_page_leads_to_the_apps_home(
    browser: Browser,
) -> None:
    username = f"consent-{secrets.token_hex(4)}"
    password = create_account(username)
    page = browser.new_context().new_page()

    page.goto(f"{APP}/login?next=%2F")
    page.get_by_role("main").get_by_role(
        "link", name=re.compile("sign in", re.I)
    ).first.click()
    page.wait_for_url(re.compile(r"/user/login"))
    page.locator('input[name="user_name"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator("form button.primary, form button[type=submit]").first.click()
    page.wait_for_url(re.compile(r"/user/settings/change_password"))
    fresh = f"Pw-{secrets.token_hex(8)}"
    page.locator('input[name="password"]').fill(fresh)
    page.locator('input[name="retype"]').fill(fresh)
    page.locator("form button.primary, form button[type=submit]").first.click()
    page.wait_for_url(re.compile(r"/login/oauth/"))
    expect(page.get_by_text("Unicon asks for this once")).to_be_visible()

    page.get_by_role("navigation", name="Unicon").get_by_role(
        "link", name="Back to Unicon"
    ).click()

    page.wait_for_url(f"{APP}/")
    expect(page.get_by_role("main")).to_be_visible()
    assert "/login/oauth/" not in page.url
