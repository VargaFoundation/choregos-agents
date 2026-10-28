# Contributing

## Layout

```
plugins/<name>/
  plugin.yaml          # kind: Plugin — name, version, core range, contents
  agents/*.yaml
  skills/<skill>/SKILL.md
  templates/<template>/manifest.yaml
  evals/*.yaml
  procedures/*.md
  servers/<server>.yaml  # optional: an MCP server image and the operations it exposes
  server/                # optional: the server's source, built and signed by CI
  README.md
```

## Rules

- **Everything in English**: code, docs, commit messages.
- **A published version is immutable.** Change something, bump `metadata.version`.
- **A server never asks for more than it needs.** Every tool it serves is declared in `operations`,
  with its access (`read` or `write`); a tool that is not declared is refused by the platform. A write
  a customer or the public can see carries `human_approval: true`.
- **Fakes are mandatory.** A server runs without the real system (a fake mode) so its tests and the
  platform's demo need no account.
- **No secret in the repository.** Configuration reaches a server through the platform's sealed secrets.
- **Every commit carries `Signed-off-by`** (`git commit -s`): the [DCO](DCO).

## Checks

Every pull request runs `choregos plugin validate` and a reproducible `pack` on each plugin, the tests
of each server, the publication script's tests and an offline dry run of it, gitleaks and the DCO.

## Publishing

On every push to `main` (or a manual run on `main`), `.github/workflows/publish.yml` runs
`scripts/publish.py`. For each plugin whose `metadata.version` is not in the registry yet:

1. **Its servers.** A server whose `image` is the placeholder
   `ghcr.io/vargafoundation/choregos-agents/<image>@sha256:0000…0000` is built from `server/<server>/`
   (or `server/` when the plugin has one server), pushed as `<image>:<plugin version>` and signed.
   The image repository is the one the server file names; it must live under
   `ghcr.io/vargafoundation/choregos-agents/` and not be a plugin's name or `index`. An image already
   pinned to a real digest is used as it is.
2. **The digest goes into the packed descriptor, not into git.** The declared files are copied to a
   staging directory, the placeholder replaced there by the image's digest, and the copy is packed with
   the core's `choregos plugin pack`. The core's format does not need the source to carry the digest:
   `pack` treats `image` as any other byte of the server file, and the content digest the index records
   is the packed archive's, which holds the real digest. The repository keeps the placeholder, which is
   never pullable, so nothing generated is committed back. Consequence: for a plugin with a server, the
   digest `choregos plugin validate` prints on the tree is not the published one.
3. **The plugin.** ORAS pushes the archive as `ghcr.io/vargafoundation/choregos-agents/<plugin>:<version>`
   (one layer, `application/vnd.choregos.plugin.v1.tar+gzip`); cosign signs its manifest digest.
4. **The index.** The published `ghcr.io/vargafoundation/choregos-agents/index` (tag `latest`, one
   layer `application/vnd.choregos.plugin-index.v1+yaml`) is read, the new versions added — every
   version ever published stays listed, with its digest and `core` range — and it is pushed and signed.

Signatures are keyless, with the workflow's GitHub OIDC token: the identity is
`https://github.com/VargaFoundation/choregos-agents/.github/workflows/publish.yml@refs/heads/main`, the
issuer `https://token.actions.githubusercontent.com`, as in the README's trust policy.

**A published version is immutable.** When `<plugin>:<version>` already exists, the published archive is
pulled and the tree — with the images the published version pins — must give the same content digest;
otherwise the run fails before anything is pushed: bump `metadata.version`. A change to a server's
source alone (`server/`) is not part of the plugin's content, so it is not detected: bump the version
with it, or the new code never ships. A run that stopped half-way is safe to replay: an image tag
already pushed is reused, not rebuilt.

A new GHCR package is private until an organisation owner makes it public (package settings →
visibility); do it once for each new plugin, image and the index, or deployments cannot pull them.

To try the publication without a registry:

```sh
cd <core checkout> && uv sync --package choregos-cli
uv run --package choregos-cli python <this repo>/scripts/publish.py <this repo>/plugins \
    --registry ghcr.io/vargafoundation/choregos-agents --offline --work /tmp/publish
uv run --package choregos-cli --with pytest pytest <this repo>/scripts/tests
```
