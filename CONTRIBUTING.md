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

Every pull request runs `choregos plugin validate` on each plugin, the tests of each server, and
gitleaks. On `main`, CI builds each server image, signs it, packs each plugin, signs it, and publishes
the index.
