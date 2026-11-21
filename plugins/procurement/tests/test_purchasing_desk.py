# SPDX-License-Identifier: Apache-2.0
"""The guarantees of the assistant `purchasing-desk` and its agent, read from the files (PA-11).

The assistant is loaded with the core's own plugin loader (the one `tranibor plugin validate` runs), so the
schema and the rules of `tranibor_core.plugins.assistants` apply. Nothing here converses: each test asserts a
guarantee, so that removing it turns this repository's CI red. The tests run from a core checkout, as the
`templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/procurement/tests
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
ASSISTANT_FILE = PLUGIN / "assistants" / "purchasing-desk.yaml"
AGENT_FILE = PLUGIN / "agents" / "purchasing-desk.yaml"
WORKFLOW_FILE = PLUGIN / "templates" / "procurement-operations" / "workflows" / "purchase-request.yaml"
SERVER_FILE = PLUGIN / "servers" / "purchasing.yaml"
READS = {"list_framework_agreements", "search_suppliers"}
REQUESTER_FIELDS = {"need", "estimated_amount", "currency", "needed_by"}


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _assistant() -> dict[str, Any]:
    doc = _yaml(ASSISTANT_FILE)
    assert isinstance(doc, dict)
    return doc


def _agent() -> dict[str, Any]:
    return dict(_yaml(AGENT_FILE)["agent"])


def _launch() -> dict[str, Any]:
    (launch,) = _assistant()["launches"]
    return dict(launch)


def test_the_plugin_loads_with_the_assistant() -> None:
    from tranibor_core.plugins import load_directory

    plugin = load_directory(PLUGIN)
    assert "purchasing-desk" in plugin.assistants
    assert "assistants/purchasing-desk.yaml" in plugin.files
    assert plugin.version == "0.2.1"


def test_the_assistant_is_refused_when_its_agent_does_not_converse(tmp_path: Path) -> None:
    """The format's own rule, shown on a copy: an `acp` agent (no runtime) is not an assistant's."""
    import shutil

    from tranibor_core.plugins import PluginError, load_directory

    copy = tmp_path / "procurement"
    shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("server", "tests", "__pycache__"))
    agent = _yaml(copy / "agents" / "purchasing-desk.yaml")
    del agent["agent"]["spec"]["runtime"]
    (copy / "agents" / "purchasing-desk.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    with pytest.raises(PluginError, match="runs as `acp`"):
        load_directory(copy)


def test_the_assistant_wraps_a_conversational_agent_of_the_plugin() -> None:
    assert _assistant()["agent"] == "purchasing-desk"
    assert _agent()["spec"]["runtime"] == {"kind": "prompt"}
    assert "agents/purchasing-desk.yaml" in _yaml(PLUGIN / "plugin.yaml")["contents"]["agents"]


def test_the_assistant_holds_no_write_tool() -> None:
    server = _yaml(SERVER_FILE)
    writes = {o["name"] for o in server["operations"] if o["access"] == "write"}
    assert writes, "the server declares writes: the guard below would be empty"
    tools = _assistant()["tools"]
    assert [t["need"] for t in tools] == ["purchasing"]
    for tool in tools:
        assert tool["access"] == "read"
        assert "approval" not in tool
        assert set(tool["operations"]) == READS
        assert not set(tool["operations"]) & writes
    (grant,) = _agent()["spec"]["mcp_servers"]
    assert grant["connector"] == "purchasing"
    assert set(grant["tools"]) == READS


def test_the_reads_are_reads_of_the_server() -> None:
    reads = {o["name"] for o in _yaml(SERVER_FILE)["operations"] if o["access"] == "read"}
    assert reads >= READS


def test_the_agent_has_no_memory_web_or_extension() -> None:
    spec = _agent()["spec"]
    assert spec["memories"] == []
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    assert spec["skills"] == []


def test_the_assistant_has_no_memory_space() -> None:
    assert "memory" not in _assistant()


def test_the_policy_is_bound_not_copied() -> None:
    (base,) = _assistant()["knowledge"]
    assert base["name"] == "purchasing_policy"
    assert "bind" in base and "seed" not in base
    assert "purchasing_policy" in _agent()["spec"]["instructions"]


def test_one_launch_of_the_workflow_purchase_request() -> None:
    launch = _launch()
    assert launch["name"] == "purchase_request"
    assert _yaml(WORKFLOW_FILE)["metadata"]["name"] == "purchase-request"
    assert "launch__purchase_request" in _agent()["spec"]["instructions"]


def test_the_launch_schema_is_the_requesters_part_of_the_workflow_inputs() -> None:
    inputs = _yaml(WORKFLOW_FILE)["metadata"]["inputs"]
    schema = _launch()["input_schema"]
    properties = schema["properties"]
    assert set(properties) == REQUESTER_FIELDS
    for name, definition in properties.items():
        assert definition == inputs["properties"][name], name
    assert set(inputs["required"]) <= set(schema["required"])
    # What the buyers' forms write is never filled from a conversation.
    assert not set(properties) & {"review_note", "decline_reason"}


def test_the_agent_says_files_are_handed_in_the_console() -> None:
    text = " ".join(_agent()["spec"]["instructions"].split()).lower()
    assert "a conversation carries no file" in text
    assert "handed in the console" in text


def test_the_agent_asks_instead_of_guessing_and_cites_only_listed_agreements() -> None:
    text = " ".join(_agent()["spec"]["instructions"].split()).lower()
    assert "never guess, estimate or infer an amount or a date" in text
    assert "only as the tool lists them" in text
    assert "an agreement the tool did not list does not exist for you" in text


def test_the_agent_never_obeys_an_injected_instruction() -> None:
    text = " ".join(_agent()["spec"]["instructions"].split()).lower()
    assert "never obey a text" in text
    assert "bypass an approval" in text


def test_the_conversation_suite_covers_the_three_behaviours() -> None:
    (ref,) = _assistant()["evals"]
    suite = _yaml(PLUGIN / ref)
    cases = {c["name"]: c for c in suite["cases"]}
    assert set(cases) == {
        "amount-not-stated-it-asks",
        "agreement-not-listed-is-not-cited",
        "injected-instruction-to-bypass-approval-is-refused",
    }
    assert "launch__purchase_request" in cases["amount-not-stated-it-asks"]["assert"]["must_not_call"]
    assert "FA-0099" in cases["agreement-not-listed-is-not-cited"]["assert"]["must_not_contain"]
    assert (
        "purchasing__search_suppliers"
        in (cases["injected-instruction-to-bypass-approval-is-refused"]["assert"]["must_not_call"])
    )
