# SPDX-License-Identifier: Apache-2.0
"""Publish every plugin whose version is not yet in the registry, then the signed index (A-01b).

Run with the core's environment, from `.github/workflows/publish.yml`:

    uv run --package choregos-cli python scripts/publish.py plugins \\
        --registry ghcr.io/vargafoundation/choregos-agents --source-url https://github.com/…

For each plugin directory, in name order:

1. **Plan.** `<registry>/<name>:<version>` exists: the published archive is pulled, and the plugin in the
   tree, with the server images the published one pins, must have the same content digest — a published
   version is immutable, so a change without a version bump fails the run before anything is pushed.
   Otherwise the version is to publish.
2. **Servers.** For a version to publish, each server whose image is still the placeholder
   (`…@sha256:0000…`) is built from its source (`server/<server>/` or, for a plugin with one server,
   `server/`), pushed as `<image repository>:<plugin version>` and signed; an image tag already there
   (a run that stopped half-way) is reused, not rebuilt.
3. **Pack.** The declared files are copied to a staging directory, the placeholder replaced by the
   image's digest in the server file, and packed with the core's `pack`: the archive, and so the content
   digest the index records, carries the real digest. The repository keeps the placeholder.
4. **Push and sign.** ORAS pushes the archive (one layer, `application/vnd.choregos.plugin.v1.tar+gzip`);
   cosign signs its manifest digest (keyless in CI).
5. **Index.** The published index (`<registry>/index:latest`) is read and every release added to it;
   a version it already lists with another digest fails the run. Versions no longer in the tree stay
   listed: they are still published. The index is pushed (`application/vnd.choregos.plugin-index.v1+yaml`)
   and signed when it changed.

`--offline` runs steps 1 to 3 and builds the index without a registry, docker or cosign, every image a
fake digest: what CI checks on a pull request.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from choregos_core.plugins import Plugin, PluginError, load_directory, opened, pack, plugin_files
from choregos_core.plugins.sources import INDEX_MEDIA_TYPE, PLUGIN_MEDIA_TYPE, SourceError, parse_index

PLACEHOLDER = "sha256:" + "0" * 64
OFFLINE_DIGEST = "sha256:" + "1" * 64
INDEX_TAG = "latest"


class Refused(Exception):
    """A run that must stop: nothing after it is pushed."""


@dataclass
class Release:
    name: str
    version: str
    digest: str
    core: str
    description: str
    oci: str

    def entry(self) -> dict[str, Any]:
        return {"digest": self.digest, "core": self.core, "description": self.description, "oci": self.oci}


@dataclass
class Plan:
    root: Path
    plugin: Plugin
    ref: str
    #: the published release, when the version is already in the registry
    published: Release | None = None
    images: dict[str, str] = field(default_factory=dict)


def run(*argv: str, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(argv), flush=True)
    return subprocess.run(argv, check=check, text=True, capture_output=capture)


class Registry:
    """ORAS, docker buildx and cosign, each called as a command; credentials come from `docker login`."""

    def __init__(self, sign: bool) -> None:
        self.sign_enabled = sign

    def digest_of(self, ref: str) -> str | None:
        """The manifest digest under `ref`, `None` when the registry has no such tag."""
        done = run("oras", "manifest", "fetch", "--descriptor", ref, check=False)
        if done.returncode == 0:
            return str(json.loads(done.stdout)["digest"])
        if "not found" in (done.stderr + done.stdout).lower():
            return None
        raise Refused(f"`{ref}`: the registry did not answer: {done.stderr.strip()}")

    def pull(self, ref: str, media_type: str, target: Path) -> Path:
        """The single layer of `media_type` of the artifact `ref`, written under `target`."""
        manifest = json.loads(run("oras", "manifest", "fetch", ref).stdout)
        layers = [layer for layer in manifest.get("layers") or [] if layer.get("mediaType") == media_type]
        if len(layers) != 1:
            raise Refused(f"`{ref}`: holds {len(layers)} layer(s) of type {media_type}, not one")
        repository = ref.rsplit(":", 1)[0]
        out = target / "layer"
        run("oras", "blob", "fetch", f"{repository}@{layers[0]['digest']}", "--output", str(out))
        return out

    def push(self, ref: str, path: Path, media_type: str, annotations: dict[str, str]) -> str:
        argv = ["oras", "push", ref, "--artifact-type", media_type]
        for key, value in sorted(annotations.items()):
            argv += ["--annotation", f"{key}={value}"]
        # Pushed from its own directory, so the layer's title is the file name, not a runner path.
        subprocess.run([*argv, f"{path.name}:{media_type}"], check=True, cwd=path.parent)
        digest = self.digest_of(ref)
        if digest is None:
            raise Refused(f"`{ref}`: pushed, then not found")
        return digest

    def build(self, context: Path, tag: str, labels: dict[str, str]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "metadata.json"
            argv = ["docker", "buildx", "build", "--push", "--tag", tag, "--metadata-file", str(metadata)]
            for key, value in sorted(labels.items()):
                argv += ["--label", f"{key}={value}"]
            run(*argv, str(context), capture=False)
            return str(json.loads(metadata.read_text())["containerimage.digest"])

    def sign(self, pinned: str) -> None:
        if self.sign_enabled:
            run("cosign", "sign", "--yes", pinned, capture=False)


# -- staging -------------------------------------------------------------------------------------------


def stage(plan: Plan, staging: Path) -> Path:
    """The plugin's declared files under `staging/<name>`, each server's placeholder replaced by the
    image `plan.images` gives it. Only the `image:` value changes: every other byte is the tree's."""
    target = staging / plan.plugin.name
    shutil.rmtree(target, ignore_errors=True)
    for name, source in plugin_files(plan.root).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
    for ref in plan.plugin.manifest["contents"].get("servers", []):
        path = target / ref
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared, wanted = str(doc["image"]), plan.images.get(str(doc["name"]))
        if wanted is None or wanted == declared:
            continue
        text = path.read_text(encoding="utf-8")
        if text.count(declared) != 1:
            raise Refused(f"{plan.plugin.name}/{ref}: the image `{declared}` must appear exactly once")
        path.write_text(text.replace(declared, wanted), encoding="utf-8")
        if yaml.safe_load(path.read_text(encoding="utf-8"))["image"] != wanted:
            raise Refused(f"{plan.plugin.name}/{ref}: the image could not be pinned")
    return target


def server_source(root: Path, server: str, servers: int) -> Path:
    for candidate in (root / "server" / server, root / "server") if servers == 1 else (root / "server" / server,):
        if (candidate / "Dockerfile").is_file():
            return candidate
    raise Refused(f"{root.name}: no Dockerfile for the server `{server}` (server/{server}/ or server/)")


# -- the run ---------------------------------------------------------------------------------------------


def plan_of(root: Path, registry: str, client: Registry | None, work: Path) -> Plan:
    plugin = load_directory(root)
    plan = Plan(root, plugin, f"{registry}/{plugin.name}:{plugin.version}")
    for sname, server in plugin.servers.items():
        repository, _, digest = str(server["image"]).partition("@")
        if digest == PLACEHOLDER and not repository.startswith(f"{registry}/"):
            raise Refused(f"{plugin.name}/{sname}: an image CI builds lives under {registry}/")
        if repository.removeprefix(f"{registry}/") in {"index", plugin.name}:
            raise Refused(f"{plugin.name}/{sname}: the image repository collides with a plugin or the index")
    if client is None or client.digest_of(plan.ref) is None:
        return plan
    # Published: the tree, with the published images, must be what was published.
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        archive = client.pull(plan.ref, PLUGIN_MEDIA_TYPE, Path(tmp))
        with opened(archive.rename(archive.with_suffix(".tar.gz"))) as published:
            plan.images = {name: str(s["image"]) for name, s in published.servers.items()}
            plan.published = Release(
                published.name,
                published.version,
                published.digest,
                published.core,
                str(published.manifest["metadata"]["description"]),
                plan.ref,
            )
    staged = load_directory(stage(plan, work / "check"))
    if staged.digest != plan.published.digest:
        raise Refused(
            f"{plugin.name} {plugin.version} is published with the digest {plan.published.digest}; the tree "
            f"gives {staged.digest}. A published version is immutable: bump metadata.version."
        )
    return plan


def publish(plan: Plan, client: Registry | None, work: Path, source_url: str) -> Release:
    plugin = plan.plugin
    for sname, server in sorted(plugin.servers.items()):
        repository, _, digest = str(server["image"]).partition("@")
        if digest != PLACEHOLDER:
            continue  # pinned in the tree: a published image, used as it is
        if client is None:
            plan.images[sname] = f"{repository}@{OFFLINE_DIGEST}"
            continue
        context = server_source(plan.root, sname, len(plugin.servers))
        tag = f"{repository}:{plugin.version}"
        built = client.digest_of(tag)
        if built is None:
            built = client.build(
                context,
                tag,
                {
                    "org.opencontainers.image.source": source_url,
                    "org.opencontainers.image.version": plugin.version,
                    "org.opencontainers.image.title": f"{plugin.name}/{sname}",
                    "org.opencontainers.image.licenses": str(plugin.manifest["metadata"]["license"]),
                },
            )
        client.sign(f"{repository}@{built}")
        plan.images[sname] = f"{repository}@{built}"
    staged = stage(plan, work / "stage")
    archive = work / "out" / f"{plugin.name}-{plugin.version}.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    packed = pack(staged, archive)
    for sname, server in packed.servers.items():
        if str(server["image"]).endswith(PLACEHOLDER):
            raise Refused(f"{plugin.name}/{sname}: still the placeholder digest once packed")
    print(f"packed {archive.name} {packed.digest}", flush=True)
    if client is not None:
        manifest = client.push(
            plan.ref,
            archive,
            PLUGIN_MEDIA_TYPE,
            {
                "org.opencontainers.image.source": source_url,
                "org.opencontainers.image.version": plugin.version,
                "org.opencontainers.image.description": f"Choregos plugin {plugin.name}, {packed.digest}",
            },
        )
        client.sign(f"{plan.ref.rsplit(':', 1)[0]}@{manifest}")
    return Release(
        packed.name,
        packed.version,
        packed.digest,
        packed.core,
        str(packed.manifest["metadata"]["description"]),
        plan.ref,
    )


def merged_index(previous: dict[str, Any] | None, releases: list[Release]) -> dict[str, Any]:
    plugins: dict[str, Any] = json.loads(json.dumps((previous or {}).get("plugins") or {}))
    for release in releases:
        versions = plugins.setdefault(release.name, {"versions": {}})["versions"]
        known = versions.get(release.version)
        if known is not None and known.get("digest") != release.digest:
            raise Refused(
                f"the index lists {release.name} {release.version} as {known.get('digest')}, the registry "
                f"holds {release.digest}"
            )
        versions[release.version] = release.entry()
    index = {"apiVersion": "choregos/v1", "kind": "PluginIndex", "plugins": plugins}
    try:
        parse_index(index)  # plugin-index.schema.json, as a deployment reads it
    except SourceError as exc:
        raise Refused(f"the index is not valid: {exc}") from exc
    return index


def publish_all(plugins_dir: Path, registry: str, client: Registry | None, work: Path, source_url: str) -> int:
    """The whole run; 0 when everything is published and indexed, 1 (with the reasons) otherwise."""
    work.mkdir(parents=True, exist_ok=True)
    try:
        # 1. Plan everything before anything is pushed.
        roots = sorted(p for p in plugins_dir.iterdir() if (p / "plugin.yaml").is_file())
        plans = [plan_of(root, registry, client, work) for root in roots]
        names = [p.plugin.name for p in plans]
        if len(set(names)) != len(names):
            raise Refused(f"two directories hold the same plugin: {names}")
        index_ref = f"{registry}/index:{INDEX_TAG}"
        previous = None
        if client is not None and client.digest_of(index_ref) is not None:
            with tempfile.TemporaryDirectory(dir=work) as tmp:
                raw = client.pull(index_ref, INDEX_MEDIA_TYPE, Path(tmp)).read_text(encoding="utf-8")
            try:
                parse_index(raw)
            except SourceError as exc:
                raise Refused(f"`{index_ref}`: the published index is not valid: {exc}") from exc
            previous = yaml.safe_load(raw)
        listed = (previous or {}).get("plugins") or {}
        for plan in plans:
            name, version = plan.plugin.name, plan.plugin.version
            if plan.published is None and version in (listed.get(name) or {}).get("versions", {}):
                raise Refused(f"the index lists {name} {version}, the registry does not hold `{plan.ref}`")
        merged_index(previous, [p.published for p in plans if p.published is not None])
        for plan in plans:
            state = "published, identical" if plan.published else "to publish"
            print(f"{plan.plugin.name} {plan.plugin.version}: {state}", flush=True)

        # 2-4. Publish what is not published.
        releases = [p.published or publish(p, client, work, source_url) for p in plans]

        # 5. The index.
        index = merged_index(previous, releases)
        out = work / "out" / "index.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.safe_dump(index, sort_keys=True, allow_unicode=True), encoding="utf-8")
        print(out.read_text(encoding="utf-8"), flush=True)
        if client is None:
            return 0
        if previous == index:
            print("the index is unchanged", flush=True)
            return 0
        digest = client.push(index_ref, out, INDEX_MEDIA_TYPE, {"org.opencontainers.image.source": source_url})
        client.sign(f"{registry}/index@{digest}")
        print(f"index {index_ref} {digest}", flush=True)
        return 0
    except (Refused, PluginError) as exc:
        problems = exc.problems if isinstance(exc, PluginError) else [str(exc)]
        for problem in problems:
            print(f"::error::{problem}", flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("plugins", type=Path, help="the directory whose subdirectories are plugins")
    parser.add_argument("--registry", required=True, help="e.g. ghcr.io/vargafoundation/choregos-agents")
    parser.add_argument("--source-url", default="https://github.com/VargaFoundation/choregos-agents")
    parser.add_argument("--work", type=Path, help="where to stage and pack (a temporary directory)")
    parser.add_argument("--offline", action="store_true", help="no registry, docker nor cosign")
    parser.add_argument("--no-sign", action="store_true", help="push without signing")
    args = parser.parse_args()
    client = None if args.offline else Registry(sign=not args.no_sign)
    work = args.work or Path(tempfile.mkdtemp(prefix="choregos-publish-"))
    return publish_all(args.plugins, args.registry.rstrip("/"), client, work, args.source_url)


if __name__ == "__main__":
    sys.exit(main())
