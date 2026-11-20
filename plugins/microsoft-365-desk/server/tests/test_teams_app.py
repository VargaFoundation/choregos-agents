# SPDX-License-Identifier: Apache-2.0
"""`teams/build_app.py`: the Teams app package holds a filled manifest and two icons of the sizes Teams asks."""

from __future__ import annotations

import importlib.util
import json
import struct
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "teams" / "build_app.py"
CLIENT = "2f6c1b8e-0d3a-4c51-9a3e-5b2b8d1e4f70"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_teams_app", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def test_the_package_holds_a_filled_manifest_and_the_two_icons(tmp_path: Path) -> None:
    built = _script().build(CLIENT, "Contoso IT", "https://contoso.example/", tmp_path / "it-desk.zip")
    with zipfile.ZipFile(built) as package:
        assert sorted(package.namelist()) == ["color.png", "manifest.json", "outline.png"]
        manifest = json.loads(package.read("manifest.json"))
        assert (manifest["id"], manifest["bots"][0]["botId"], manifest["webApplicationInfo"]["id"]) == (CLIENT,) * 3
        assert manifest["developer"]["privacyUrl"] == "https://contoso.example/privacy"
        assert manifest["authorization"]["permissions"]["resourceSpecific"] == [
            {"name": "ChannelMessage.Read.Group", "type": "Application"}
        ]
        assert _size(package.read("color.png")) == (192, 192)
        assert _size(package.read("outline.png")) == (32, 32)
    assert "<" not in json.dumps(manifest), "no placeholder left"


@pytest.mark.parametrize(
    ("client", "developer", "website"),
    [
        ("not-a-guid", "Contoso IT", "https://contoso.example"),
        (CLIENT, "Contoso IT", "http://contoso.example"),
        (CLIENT, " ", "https://contoso.example"),
        (CLIENT, "<your organisation>", "https://contoso.example"),
    ],
    ids=["client-not-a-guid", "site-not-https", "no-developer", "developer-a-placeholder"],
)
def test_a_manifest_that_teams_would_refuse_is_not_built(client: str, developer: str, website: str) -> None:
    with pytest.raises(ValueError):
        _script().manifest(client, developer, website)


def test_the_command_line_refuses_with_a_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = _script().main(["--client-id", "x", "--developer", "C", "--website", "https://c.example"])
    assert code == 2 and "refused:" in capsys.readouterr().err
