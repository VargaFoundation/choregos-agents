# SPDX-License-Identifier: Apache-2.0
"""The automated check of the Entra application: its permissions are exactly those of its stage.

The application is single-tenant, asks Microsoft Graph for DELEGATED permissions only (`Scope`, never an
application permission, `Role`), and asks exactly the stage's set:

- stage 2 (calendar): `Calendars.Read`, `offline_access`;
- stage 3 (calendar, mail, chats): stage 2 plus `Mail.Read`, `Chat.Read`.

Its redirect URIs are https and end with the platform's consent callback; implicit grant is off.

Usage, on the application as Entra holds it (`az ad app show --id <client id> > app.json`, or the manifest
downloaded from the portal, which has the same `requiredResourceAccess`)::

    python -m choregos_microsoft_365.entra --stage 3 app.json

It prints each problem and exits 1, or prints `ok` and exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
#: Microsoft Graph's delegated permission ids (oauth2PermissionScopes), the same in every tenant.
#: Check them in yours: `az ad sp show --id 00000003-0000-0000-c000-000000000000
#: --query "oauth2PermissionScopes[?value=='Chat.Read'].id"`.
SCOPES = {
    "offline_access": "7427e0e9-2fba-42fe-b0c0-848c9e6a8182",  # gitleaks:allow - a public permission id, not a key
    "Calendars.Read": "465a38f9-76ea-45b9-9f34-9e8b0d4b0b42",
    "Mail.Read": "570282fd-fa5c-430d-a7fd-fc8dc98a9dca",
    "Chat.Read": "f501c180-9344-439a-bca0-6cbf209fd270",
}
STAGES: dict[int, frozenset[str]] = {
    2: frozenset({"Calendars.Read", "offline_access"}),
    3: frozenset({"Calendars.Read", "offline_access", "Mail.Read", "Chat.Read"}),
}
CALLBACK_PATH = "/api/v1/me/connected-accounts/callback"
_NAMES = {v: k for k, v in SCOPES.items()}


def problems(app: dict[str, Any], stage: int) -> list[str]:
    """What breaks the stage's rules; empty when the application is exactly what the stage needs."""
    if stage not in STAGES:
        return [f"unknown stage {stage}: {sorted(STAGES)}"]
    found: list[str] = []
    if app.get("signInAudience") != "AzureADMyOrg":
        found.append(f"signInAudience is {app.get('signInAudience')!r}: single tenant (AzureADMyOrg) expected")

    resources = app.get("requiredResourceAccess") or []
    asked: set[str] = set()
    for resource in resources:
        app_id = resource.get("resourceAppId")
        accesses = resource.get("resourceAccess") or []
        if app_id != GRAPH_APP_ID:
            found.append(f"a permission on another API than Microsoft Graph: {app_id}")
            continue
        for access in accesses:
            name = _NAMES.get(str(access.get("id")), str(access.get("id")))
            if access.get("type") != "Scope":
                found.append(f"{name}: an application permission ({access.get('type')}); delegated only (Scope)")
            else:
                asked.add(name)
    wanted = STAGES[stage]
    for name in sorted(asked - wanted):
        found.append(f"{name}: not a permission of stage {stage}")
    for name in sorted(wanted - asked):
        found.append(f"{name}: missing for stage {stage}")

    web = app.get("web") or {}
    uris = web.get("redirectUris") or []
    if not uris:
        found.append(f"no web redirect URI: the platform's consent callback ({CALLBACK_PATH}) is expected")
    for uri in uris:
        if not (isinstance(uri, str) and uri.startswith("https://") and uri.endswith(CALLBACK_PATH)):
            found.append(f"redirect URI {uri!r}: https and ending with {CALLBACK_PATH} expected")
    for platform in ("spa", "publicClient"):
        if (app.get(platform) or {}).get("redirectUris"):
            found.append(f"{platform} redirect URIs: a web (confidential) application only")
    implicit = web.get("implicitGrantSettings") or {}
    if implicit.get("enableAccessTokenIssuance") or implicit.get("enableIdTokenIssuance"):
        found.append("implicit grant is on: off expected")
    if app.get("isFallbackPublicClient"):
        found.append("public client flows are allowed: off expected")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Entra application of the microsoft-365 plugin.")
    parser.add_argument("--stage", type=int, choices=sorted(STAGES), required=True)
    parser.add_argument("app", type=Path, help="the application as JSON (az ad app show, or the portal's manifest)")
    arguments = parser.parse_args(argv)
    found = problems(json.loads(arguments.app.read_text(encoding="utf-8")), arguments.stage)
    for problem in found:
        print(problem)
    if not found:
        print("ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
