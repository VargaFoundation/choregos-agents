# MCP clients plugin

The external agents of the core's built-in catalogue (ADR 0040, ADR 0033), unchanged: the assistants and editors that act through Choregos's MCP door with their person's rights, never more. They have no instructions and no evals.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`chatgpt`](agents/chatgpt.yaml) | 1 | ChatGPT acting through the MCP door, with its person's rights intersected with this agent's. |
| [`claude-ai`](agents/claude-ai.yaml) | 1 | Claude (claude.ai) acting through the MCP door, with its person's rights intersected with this agent's. |
| [`claude-code`](agents/claude-code.yaml) | 1 | Claude Code acting through the MCP door, with its person's rights intersected with this agent's. |
| [`claude-desktop`](agents/claude-desktop.yaml) | 1 | Claude Desktop acting through the MCP door, with its person's rights intersected with this agent's. |
| [`copilot-vscode`](agents/copilot-vscode.yaml) | 1 | GitHub Copilot in VS Code acting through the MCP door, with its person's rights intersected with this agent's. |
| [`cursor`](agents/cursor.yaml) | 1 | Cursor acting through the MCP door, with its person's rights intersected with this agent's. |

Version 1.0.0, core `>=0.21.0,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).

```sh
choregos plugin validate plugins/mcp-clients
choregos plugin pack plugins/mcp-clients -o mcp-clients-1.0.0.tar.gz   # same bytes every time
```
