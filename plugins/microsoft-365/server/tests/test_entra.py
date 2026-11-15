# SPDX-License-Identifier: Apache-2.0
"""The automated check of the Entra application: exactly the stage's delegated permissions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from choregos_microsoft_365.entra import GRAPH_APP_ID, SCOPES, main, problems

ENTRA = Path(__file__).resolve().parents[1] / "entra"


def app(stage: int) -> dict[str, Any]:
    loaded = json.loads((ENTRA / f"stage-{stage}.json").read_text())
    assert isinstance(loaded, dict)
    return loaded


def scopes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    accesses = manifest["requiredResourceAccess"][0]["resourceAccess"]
    assert isinstance(accesses, list)
    return accesses


@pytest.mark.parametrize("stage", [2, 3])
def test_the_shipped_applications_are_exactly_their_stage(stage: int) -> None:
    assert problems(app(stage), stage) == []


def test_stage_3_is_too_wide_for_stage_2_and_stage_2_too_narrow_for_3() -> None:
    assert problems(app(3), 2) == ["Chat.Read: not a permission of stage 2", "Mail.Read: not a permission of stage 2"]
    assert problems(app(2), 3) == ["Chat.Read: missing for stage 3", "Mail.Read: missing for stage 3"]


def test_an_application_permission_is_refused() -> None:
    manifest = app(2)
    scopes(manifest)[0]["type"] = "Role"
    assert any("application permission" in p for p in problems(manifest, 2))


def test_entra_s_default_user_read_is_refused() -> None:
    manifest = app(2)
    scopes(manifest).append({"id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope"})
    assert problems(manifest, 2) == ["e1fe6dd8-ba31-4d61-89e7-88639da4683d: not a permission of stage 2"]


def test_another_api_is_refused() -> None:
    manifest = app(2)
    manifest["requiredResourceAccess"].append({"resourceAppId": "00000002-0000-0ff1-ce00-000000000000"})
    assert problems(manifest, 2) == [
        "a permission on another API than Microsoft Graph: 00000002-0000-0ff1-ce00-000000000000"
    ]


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda m: m.update(signInAudience="AzureADMultipleOrgs"), "single tenant"),
        (
            lambda m: m["web"].update(
                redirectUris=["http://choregos.example.com/api/v1/me/connected-accounts/callback"]
            ),
            "https",
        ),
        (lambda m: m["web"].update(redirectUris=["https://choregos.example.com/elsewhere"]), "ending with"),
        (lambda m: m["web"].update(redirectUris=[]), "no web redirect URI"),
        (lambda m: m.update(spa={"redirectUris": ["https://x.example/"]}), "spa redirect URIs"),
        (lambda m: m["web"]["implicitGrantSettings"].update(enableAccessTokenIssuance=True), "implicit grant"),
        (lambda m: m.update(isFallbackPublicClient=True), "public client"),
    ],
)
def test_the_application_s_other_rules(change: Any, says: str) -> None:
    manifest = copy.deepcopy(app(3))
    change(manifest)
    assert any(says in p for p in problems(manifest, 3)), problems(manifest, 3)


def test_the_shipped_files_name_graph_s_delegated_ids() -> None:
    for stage in (2, 3):
        (resource,) = app(stage)["requiredResourceAccess"]
        assert resource["resourceAppId"] == GRAPH_APP_ID
        assert {a["id"] for a in resource["resourceAccess"]} <= set(SCOPES.values())


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--stage", "3", str(ENTRA / "stage-3.json")]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert main(["--stage", "2", str(ENTRA / "stage-3.json")]) == 1
    assert "Mail.Read: not a permission of stage 2" in capsys.readouterr().out
