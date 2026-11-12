---
name: security-review
description: A security review checklist by class of flaw, with what to look for and how to cite it. Use it when reviewing a diff for security, or when a diff touches authentication, authorisation, input handling, secrets or dependencies.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Security review of a diff

Review what the diff **adds or changes**, plus the code it calls when the trust boundary is there. A finding
is `file:line — risk — fix`; it is *exploitable* (verdict `request_changes`) only if you can state the
input that triggers it.

| Class | Look for |
|:--|:--|
| Injection | SQL built by string concatenation or f-strings; shell commands with user input (`shell=True`, backticks); template, LDAP, XPath, header or log injection; prompt injection where untrusted text reaches a model with tools |
| Authentication | endpoints with no auth dependency; tokens not verified (signature, issuer, audience, expiry); sessions not rotated; credentials compared with `==` |
| Authorisation | the check done only in the UI; an object fetched by id without checking the owner or tenant; a role check missing on a new route; a check that fails open when the rights service is down |
| Input | unvalidated size, type or path (`..`, absolute paths); open redirects; SSRF through a user-supplied URL; unbounded uploads or pagination |
| Secrets | keys, tokens, passwords in code, tests, fixtures, config or logs; secrets in URLs; `.env` committed |
| Data | personal data logged or returned in full; sensitive fields not redacted; weak or home-made cryptography; predictable identifiers |
| Deserialisation | `pickle`, `yaml.load` without a safe loader, `eval`, dynamic imports from input |
| Dependencies | a new or upgraded package: unpinned, abandoned, with a known advisory; a lockfile not updated with the manifest |
| Errors | stack traces or internal details sent to the client; exceptions swallowed so a check is skipped |
| Infrastructure | containers as root, wide permissions, public exposure by default, TLS verification disabled |

## Method

1. List the entry points the diff adds (routes, jobs, handlers, CLI arguments) and what reaches them.
2. Follow each input to where it is used: is it validated, authorised, escaped for *that* sink?
3. Check what is written to logs and responses.
4. Check what the change grants by default: the safe choice must be the default.

Say **not reviewed** for anything you could not read (generated code, a service outside the repository);
never write "no issues" for what you did not examine.
