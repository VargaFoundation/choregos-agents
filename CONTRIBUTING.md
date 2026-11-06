# Contributing

## Layout

```
plugins/<name>/
  plugin.yaml          # kind: Plugin — name, version, core range, contents
  agents/*.yaml
  skills/<skill>/SKILL.md
  templates/<template>/manifest.yaml
  evals/*.yaml
  evals/matrix/*.yaml    # optional: cases of the core's eval matrix (`contents.matrix_cases`)
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
  platform's demo need no account; a server that a matrix case connects to declares that mode in its
  `fake` ([below](#matrix-cases-and-a-servers-fake)).
- **No secret in the repository.** Configuration reaches a server through the platform's sealed secrets.
- **Every commit carries `Signed-off-by`** (`git commit -s`): the [DCO](DCO).

## Checks

Every pull request runs `choregos plugin validate` and a reproducible `pack` on each plugin, the tests
of each server, the ServiceNow plugin's matrix cases played as the core's scripted eval matrix plays them
and judged by its case judge, the publication script's tests and an offline dry run of it, gitleaks and
the DCO. Every job that needs the core runs it at one commit, `CHOREGOS_REF` in `ci.yml` (the same in
`publish.yml`): move both together, to a release tag's commit.

## Matrix cases and a server's fake

A plugin may ship cases of the core's eval matrix (VargaFoundation/choregos#1372; `metadata.core` from
0.23.2, the first core whose schemas accept them): agent cases in the core's own format
(`choregos_playbooks.evals.agents`; the core's runbook `docs/runbooks/live-eval-matrix.md`, "A plugin's
cases"), declared in the manifest:

```yaml
contents:
  servers: [servers/servicenow.yaml]
  matrix_cases:
    - evals/matrix/itsm-triage-similar-incidents.yaml
```

`choregos plugin validate` checks what the matrix needs to find and play a case: a `task`, an `agent` the
plugin ships (or the case's own `agent_spec`), `tools_cells` among `mcp` and `scripts`, and each connector
that is a plugin's server written `plugin:<this plugin>/<server>` under the server's own name, since the
agent's tools are `<server>__<operation>`:

```yaml
connectors:
  servicenow: { kind: "plugin:servicenow/servicenow", scene: default }
```

The installer keeps the cases and never plays them. A server a case connects to declares how the platform
runs it on its fake:

```yaml
# servers/<server>.yaml
fake:
  env: {SERVICENOW_FAKE: "1"}   # what switches the server to its seeded fake instance
  scenes: [default]             # the scenes that fake serves (`default` when absent)
```

None of its variables may be `CHOREGOS_*` or one that `config_schema` or `secret_fields` reaches
(`validate` refuses both), so no administrator switches a server to its fake. The server must start on its
fake with exactly these variables plus the platform's token, `CHOREGOS_SERVER_TOKEN`, and nothing of its
configuration: that is how the platform starts it.

How the core's matrix plays them:

- **Scripted cell** (the core's CI, `CHOREGOS_FAKES=1`): the matrix names a case `plugin:<plugin>/<file
  stem>` and loads it from the offline plugins (`CHOREGOS_PLUGINS_OFFLINE`, else the core's
  `plugins/offline/`). It reaches the case's server as the broker does, over MCP with a bearer token, on a
  fake registered for it in the process (`choregos_adapters.fakes.plugin_servers.use_fake_plugin_server`):
  the core's CI registers a recording of the server's fake (`FakePluginServer`); without a registered fake
  the case is skipped, never failed. The server's declaration governs its tools: the declared operations
  only, their access, `human_approval` never lowered, the platform's arguments (`action_key`) filled on an
  executed write.
- **Live cell**: the eval organisation installs the plugin. Where the deployment sets
  `CHOREGOS_EVAL_FAKE_SCENES=1`, the platform runs beside the server an eval instance (the same image,
  `fake.env` and the platform's token, no configuration, no egress), and the matrix's seeding binds the
  connector nobody configured to it.

This repository plays its cases the scripted way (CI's `matrix` job):
`plugins/servicenow/server/tests/test_matrix_cases.py` points `CHOREGOS_PLUGINS_OFFLINE` at `plugins/`,
registers the server's own app on its fake (`build_app` with `fake.env` and the token, behind
`httpx.ASGITransport`), plays each case in each of its cells with the core's `eval_doors.play` and judges
it with the core's `judge`. From a core checkout, v0.23.2 or later:

```sh
cd <core checkout> && uv sync --frozen
PYTHONPATH=<this repo>/plugins/servicenow/server/src uv run --frozen pytest -q -p no:cacheprovider \
    <this repo>/plugins/servicenow/server/tests/test_matrix_cases.py
```

The core plays a published release's cases once its offline lock carries that release: a core maintainer
moves the lock with `uv run python -m tools.plugins.update_lock --plugin <plugin>@<version>`.

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
