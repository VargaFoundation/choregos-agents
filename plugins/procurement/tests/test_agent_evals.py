# SPDX-License-Identifier: Apache-2.0
"""Each agent's eval suite passes on the plugin's own instructions, as the core plays it at install.

The core's installer (`tranibor_api.services.plugin_installer._evals`) refuses a plugin whose own evals
fail: level 1 (`must_contain` / `must_not_contain` on the rendered, lower-cased instructions), then each
deterministic case of `cases`. `procurement` 0.2.0 was published with two needles split across a line of
its YAML (`quoted remark`, `the files are handed in the console`): `tranibor plugin validate` loads the
files, plays no eval, and the archive was refused by every core that installed it. These tests play the
same two levels with the core's own functions, from a core checkout (the `templates` job):

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/procurement/tests
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]


def _suites() -> list[str]:
    manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))
    return [str(ref) for ref in manifest["contents"]["evals"]]


def _suite(ref: str) -> dict[str, Any]:
    suite = yaml.safe_load((PLUGIN / ref).read_text(encoding="utf-8"))
    assert isinstance(suite, dict), ref
    return suite


def test_every_agent_of_the_plugin_has_its_suite() -> None:
    from tranibor_core.plugins import load_directory

    plugin = load_directory(PLUGIN)
    assert {str(_suite(ref)["agent"]) for ref in _suites()} == set(plugin.agents)


@pytest.mark.parametrize("ref", _suites())
def test_the_suite_passes_on_the_plugin_s_own_instructions(ref: str) -> None:
    """Level 1, then the deterministic cases: what the installer runs before it accepts the plugin."""
    from tranibor_api.schemas.agents import AgentCreate, AgentEvalCase
    from tranibor_api.services.agent_evals import evaluate as evaluate_cases
    from tranibor_core.agent_catalogue import entry_of
    from tranibor_core.agent_catalogue.evals import evaluate
    from tranibor_core.plugins import load_directory

    plugin = load_directory(PLUGIN)
    suite = _suite(ref)
    slug = str(suite["agent"])
    entry = entry_of(plugin.agents[slug], plugin.name, plugin.version)
    result = evaluate(suite, agent=entry)
    assert result.passed, f"{ref}: missing {result.missing}, forbidden {result.forbidden}"
    spec = AgentCreate.model_validate(entry.document).spec
    cases = [AgentEvalCase.model_validate(case) for case in suite.get("cases") or []]
    failing = [r.name for r in evaluate_cases(spec, cases) if not r.passed]
    assert not failing, f"{ref}: deterministic cases fail on the plugin's own instructions: {failing}"


def test_a_needle_split_across_a_line_is_caught(tmp_path: Path) -> None:
    """The defect of 0.2.0, shown on a copy: a needle wrapped across a line of the YAML fails level 1."""
    import shutil

    from tranibor_core.agent_catalogue import entry_of
    from tranibor_core.agent_catalogue.evals import evaluate
    from tranibor_core.plugins import load_directory

    copy = tmp_path / "procurement"
    shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns(".venv", "__pycache__", "server"))
    agent = copy / "agents" / "offer-reader.yaml"
    text = agent.read_text(encoding="utf-8")
    assert "quoted remark" in text
    agent.write_text(text.replace("quoted remark", "quoted\n        remark", 1), encoding="utf-8")
    plugin = load_directory(copy)
    suite = yaml.safe_load((copy / "evals" / "offer-reader.yaml").read_text(encoding="utf-8"))
    result = evaluate(suite, agent=entry_of(plugin.agents["offer-reader"], plugin.name, plugin.version))
    assert not result.passed and "quoted remark" in result.missing
