# SPDX-License-Identifier: Apache-2.0
"""`scripts/publish.py` against an in-memory registry: what CI does on `main`, without GHCR.

    cd <core> && uv run --frozen --package choregos-cli --with pytest pytest <agents>/scripts/tests
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publish  # noqa: E402
from choregos_core.plugins import opened, plugin_files  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REGISTRY = "ghcr.io/vargafoundation/choregos-agents"


class FakeRegistry(publish.Registry):
    """Tags → (manifest digest, layer media type, layer bytes); every build, push and signature recorded."""

    def __init__(self) -> None:
        super().__init__(sign=True)
        self.tags: dict[str, tuple[str, str, bytes]] = {}
        self.built: list[str] = []
        self.pushed: list[str] = []
        self.signed: list[str] = []

    def digest_of(self, ref: str) -> str | None:
        found = self.tags.get(ref)
        return found[0] if found else None

    def pull(self, ref: str, media_type: str, target: Path) -> Path:
        digest, stored, data = self.tags[ref]
        assert stored == media_type
        (target / "layer").write_bytes(data)
        return target / "layer"

    def push(self, ref: str, path: Path, media_type: str, annotations: dict[str, str]) -> str:
        data = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(media_type.encode() + data).hexdigest()
        self.tags[ref] = (digest, media_type, data)
        self.pushed.append(ref)
        return digest

    def build(self, context: Path, tag: str, labels: dict[str, str]) -> str:
        assert (context / "Dockerfile").is_file()
        digest = "sha256:" + hashlib.sha256(f"{tag}#{len(self.built)}".encode()).hexdigest()
        self.tags[tag] = (digest, "image", b"")
        self.built.append(tag)
        return digest

    def sign(self, pinned: str) -> None:
        self.signed.append(pinned)


@pytest.fixture
def plugins(tmp_path: Path) -> Path:
    """The servicenow plugin's declared files and its Dockerfile, and a plugin without a server."""
    root = tmp_path / "plugins"
    for name, source in plugin_files(REPO / "plugins" / "servicenow").items():
        (root / "servicenow" / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, root / "servicenow" / name)
    (root / "servicenow" / "server").mkdir()
    (root / "servicenow" / "server" / "Dockerfile").write_text("FROM scratch\n")
    hello = root / "hello"
    (hello / "agents").mkdir(parents=True)
    (hello / "agents" / "greeter.yaml").write_text(
        "catalogue: {version: 1, role: greet, summary: Greets.}\n"
        "agent: {slug: greeter, display_name: Greeter, spec: {instructions: Greet the author.}}\n"
    )
    _manifest(hello, "1.0.0")
    return root


def _manifest(root: Path, version: str) -> None:
    (root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "choregos/v1",
                "kind": "Plugin",
                "metadata": {
                    "name": "hello",
                    "version": version,
                    "publisher": "Varga Foundation",
                    "license": "Apache-2.0",
                    "description": "A greeter.",
                    "core": ">=0.21.0,<1.0.0",
                },
                "contents": {"agents": ["agents/greeter.yaml"]},
            }
        )
    )


def _run(plugins: Path, registry: FakeRegistry, tmp_path: Path) -> int:
    return publish.publish_all(plugins, REGISTRY, registry, tmp_path / "work", "https://example.test/repo")


def _index(registry: FakeRegistry) -> dict:
    return yaml.safe_load(registry.tags[f"{REGISTRY}/index:latest"][2])


def test_a_first_run_builds_pins_packs_pushes_signs_and_indexes(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    assert _run(plugins, registry, tmp_path) == 0

    image = f"{REGISTRY}/servicenow-mcp"
    assert registry.built == [f"{image}:0.1.0"]
    built = registry.tags[f"{image}:0.1.0"][0]
    archive = tmp_path / "work" / "out" / "servicenow-0.1.0.tar.gz"
    with opened(archive) as packed:
        assert packed.servers["servicenow"]["image"] == f"{image}@{built}"
        digest = packed.digest
    # The tree keeps the placeholder: nothing generated is written back.
    assert publish.PLACEHOLDER in (plugins / "servicenow" / "servers" / "servicenow.yaml").read_text()

    index = _index(registry)
    release = index["plugins"]["servicenow"]["versions"]["0.1.0"]
    assert release["digest"] == digest
    assert release["oci"] == f"{REGISTRY}/servicenow:0.1.0"
    assert release["core"] == ">=0.21.0,<0.23.0"
    assert set(index["plugins"]) == {"hello", "servicenow"}
    signed = set(registry.signed)
    assert f"{image}@{built}" in signed
    assert f"{REGISTRY}/servicenow@{registry.tags[f'{REGISTRY}/servicenow:0.1.0'][0]}" in signed
    assert f"{REGISTRY}/index@{registry.tags[f'{REGISTRY}/index:latest'][0]}" in signed


def test_a_second_run_pushes_nothing(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    assert _run(plugins, registry, tmp_path) == 0
    pushed, built = list(registry.pushed), list(registry.built)
    assert _run(plugins, registry, tmp_path) == 0
    assert registry.pushed == pushed
    assert registry.built == built


def test_a_published_version_is_immutable(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    assert _run(plugins, registry, tmp_path) == 0
    before = dict(registry.tags)
    agent = plugins / "hello" / "agents" / "greeter.yaml"
    agent.write_text(agent.read_text().replace("Greet the author.", "Greet everyone."))

    assert _run(plugins, registry, tmp_path) == 1
    assert registry.tags == before  # refused before anything was pushed


def test_a_new_version_joins_the_index_and_the_old_one_stays(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    assert _run(plugins, registry, tmp_path) == 0
    first = _index(registry)["plugins"]["hello"]["versions"]["1.0.0"]
    _manifest(plugins / "hello", "1.1.0")

    assert _run(plugins, registry, tmp_path) == 0
    versions = _index(registry)["plugins"]["hello"]["versions"]
    assert versions["1.0.0"] == first
    assert set(versions) == {"1.0.0", "1.1.0"}
    assert f"{REGISTRY}/hello:1.1.0" in registry.tags


def test_an_image_already_pushed_is_reused(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    tag = f"{REGISTRY}/servicenow-mcp:0.1.0"
    registry.tags[tag] = ("sha256:" + "a" * 64, "image", b"")  # a run that stopped after the image
    assert _run(plugins, registry, tmp_path) == 0
    assert registry.built == []
    with opened(tmp_path / "work" / "out" / "servicenow-0.1.0.tar.gz") as packed:
        assert packed.servers["servicenow"]["image"].endswith("@sha256:" + "a" * 64)


def test_an_index_that_disagrees_with_the_registry_is_refused(plugins: Path, tmp_path: Path) -> None:
    registry = FakeRegistry()
    assert _run(plugins, registry, tmp_path) == 0
    # The registry lost hello 1.0.0, which the index still lists.
    del registry.tags[f"{REGISTRY}/hello:1.0.0"]
    before = dict(registry.tags)
    assert _run(plugins, registry, tmp_path) == 1
    assert registry.tags == before


def test_offline_packs_and_indexes_without_a_registry(plugins: Path, tmp_path: Path) -> None:
    assert publish.publish_all(plugins, REGISTRY, None, tmp_path / "work", "https://example.test/repo") == 0
    index = yaml.safe_load((tmp_path / "work" / "out" / "index.yaml").read_text())
    assert set(index["plugins"]) == {"hello", "servicenow"}


def test_the_tree_s_plugins_publish_offline(tmp_path: Path) -> None:
    assert publish.publish_all(REPO / "plugins", REGISTRY, None, tmp_path / "work", "https://x.test") == 0
