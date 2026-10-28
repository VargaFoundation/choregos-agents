# SPDX-License-Identifier: Apache-2.0
"""The agents of these plugins are the core's built-in catalogue entries, unchanged (A-03).

Run with the core's environment (CI pins it):

    uv run --package choregos-cli python scripts/check_core_parity.py plugins

For every agent the core's catalogue ships, the plugin that carries it must give the same entry once
loaded the way the installer loads it (`entry_of`, which prepends the core's work-item context to an
internal agent's instructions): the same document, so the same instructions byte for byte, and the same
catalogue version, role, summary, outputs, tools and tags. The core's eval cases must be carried, each
passing on the plugin's agent; the core's skills must be carried with the same files. An agent whose
text differed would be offered as an update when the core moves its catalogue to these plugins
(S43-09). Remove this check once the core no longer ships the catalogue.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from choregos_core.catalogue_d_agents import entrees, entry_of, fichiers_de_la_skill, skills_du_catalogue
from choregos_core.catalogue_d_agents.evals import case_paths, evaluate, load_case
from choregos_core.plugins import load_directory


def main(plugins_dir: Path) -> int:
    agents, evals, skills = {}, {}, {}
    for root in sorted(p for p in plugins_dir.iterdir() if (p / "plugin.yaml").is_file()):
        plugin = load_directory(root)
        for slug, document in plugin.agents.items():
            agents[slug] = (plugin.name, entry_of(document))
        contents = plugin.manifest["contents"]
        for ref in contents.get("evals", []):
            evals[Path(ref).name] = (plugin.name, (root / ref).read_bytes())
        for ref in contents.get("skills", []):
            files = {p.relative_to(root / ref).as_posix(): p.read_text(encoding="utf-8")
                     for p in sorted((root / ref).rglob("*")) if p.is_file()}
            skills[Path(ref).name] = (plugin.name, files)

    problems: list[str] = []
    for core in entrees():
        if core.slug not in agents:
            problems.append(f"{core.slug}: in the core's catalogue, in no plugin")
            continue
        name, ours = agents[core.slug]
        if ours.document != core.document:
            problems.append(f"{name}/{core.slug}: the agent document differs from the core's")
        if ours.empreinte != core.empreinte:
            problems.append(f"{name}/{core.slug}: fingerprint {ours.empreinte} != core {core.empreinte}")
        for field in ("version", "role", "resume", "client", "portee", "outputs", "tools", "tags",
                      "human_gate_after", "skills", "genre"):
            if getattr(ours, field) != getattr(core, field):
                problems.append(f"{name}/{core.slug}: catalogue `{field}` differs from the core's")
    for path in case_paths():
        if path.name not in evals:
            problems.append(f"eval case {path.name}: not carried by any plugin")
            continue
        name, data = evals[path.name]
        if yaml.safe_load(data) != load_case(path):
            problems.append(f"{name}/evals/{path.name}: differs from the core's case")
        case = load_case(path)
        slug = str(case["agent"])
        if slug in agents:
            result = evaluate(case, agent=agents[slug][1])
            if not result.passed:
                problems.append(f"{name}/evals/{path.name}: fails ({result.missing}, {result.forbidden})")
    for skill in skills_du_catalogue():
        if skill not in skills:
            problems.append(f"skill {skill}: not carried by any plugin")
            continue
        name, ours = skills[skill]
        for file, text in fichiers_de_la_skill(skill).items():
            if ours.get(file) != text:
                problems.append(f"{name}/skills/{skill}/{file}: differs from the core's")
    for problem in problems:
        print(f"✗ {problem}")
    if problems:
        return 1
    print(f"✓ {len(entrees())} agents, {len(case_paths())} eval cases and {len(skills_du_catalogue())} "
          "skill(s) of the core's catalogue, identical in the plugins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
