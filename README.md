# PeopleCodeAssist

Wiring for PeopleTools 8.63 Application Designer's "AI Assist" feature: the
glue that lets PSIDE hand a developer's question or task off to an external
LLM CLI (Claude Code, Codex, Cline, Aider, ...), with PeopleSoft project
context and guardrails attached, and stream the response back into the IDE.

PSIDE itself supplies the UI, the named pipe, and (for write-enabled
sessions) the exported project snapshot; everything in this directory is
what turns that into an actual CLI invocation.

## How a request flows

1. PSIDE spawns `python Plugins/llm_plugin_<name>.py --pipe <named-pipe> <attributes>`,
   where `<attributes>` is a JSON blob (context cache dir, prompt file,
   read-only/write-enabled file lists, and `enable_write` if write-back is
   checked).
2. The adapter builds `AGENTS.md` in the context cache dir by combining
   `SystemPrompts/READ_MODE_AGENTS.md` or `WRITE_MODE_AGENTS.md` (persona,
   hard constraints, response format) with `UserRules/RULES.md`
   (project-specific PeopleCode conventions, capped at 1000 characters) --
   see `LLMPluginBase.generate_agents_file_in_workdir()`.
3. The adapter assembles that CLI's invocation and runs it, piping its
   stdout/stderr back through the named pipe to PSIDE.
4. The adapter exits with one of `LLMPluginExitCodes` so PSIDE can show a
   meaningful error if something upstream of the model failed.

## Layout

| Path | What it is |
|---|---|
| `Plugins/llm_plugin_base.py` | Shared base class (`LLMPluginBase`) + exit codes. Every adapter subclasses it. |
| `Plugins/llm_plugin_claude.py` | Claude Code adapter. Read-only; wires the MCP server below instead of file/shell tools. |
| `Plugins/llm_plugin_codex.py` | Codex CLI adapter. Same design as the Claude adapter, adapted to Codex's `exec`/`-c` config-override mechanics. |
| `Plugins/llm_plugin_cline.py`, `llm_plugin_aider.py` | Oracle-delivered adapters. Support write-back by editing `write_enabled_files` in place. |
| `Plugins/llm_plugin_probe.py` | No-op diagnostic adapter -- confirms the named-pipe handshake without invoking a model. |
| `MCPServers/ps_mcp_server.py` | Stdio MCP server over PSAIAPI. `MODE=read` (default) exposes `get_*_details`/`get_*_peoplecode` lookups; `compile`/`write` modes add compile and stage/save tools, unused by any adapter yet. |
| `Skills/peoplecode-generation/` | PeopleCode syntax + built-in-function verification guardrail. Claude/Codex bundle its reference files as static context (no tool access to fetch on demand); a tool-capable assistant could load it as an on-demand Skill instead. |
| `SystemPrompts/READ_MODE_AGENTS.md`, `WRITE_MODE_AGENTS.md` | Oracle-delivered persona/constraints, selected by whether write-back is enabled. |
| `UserRules/RULES.md` | House PeopleCode conventions, appended to every adapter's `AGENTS.md`. |

## Status

- **Read-only today.** The Claude and Codex adapters refuse `enable_write`
  outright. Write-back needs a decision on how PSIDE actually persists a
  write-enabled change -- see the `TODO(write-mode)` comment in both
  adapters before touching this.
- **Claude and Codex get live PeopleSoft metadata, not a static dump.**
  Both run with no file/shell tools; the only tool is `ps_mcp_server.py`
  (`MODE=read`), scoped via `--allowedTools`/`-c mcp_servers.*` so the model
  can look up records, pages, components, app classes, App Engine programs,
  and projects on demand instead of guessing from whatever App Designer
  happened to export.
- Requires Python 3.12+ on Windows, and the respective CLI
  (`claude`/`codex`) installed, authenticated, and on `PATH`.
- `ps_mcp_server.py` requires the `mcp` package, **pinned below 2.0**
  (`pip install "mcp<2"`), in the same Python interpreter PSIDE spawns
  plugins with. `mcp` 2.x renamed `FastMCP` to `MCPServer` and will fail
  the server's `from mcp.server.fastmcp import FastMCP` import. This is a
  one-time environment setup step, not something either adapter installs
  for you.

## Adding another adapter

Subclass `LLMPluginBase`, implement `build_command()` and `execute()`, call
`generate_agents_file_in_workdir()` before building the command, and exit
with an `LLMPluginExitCodes` value. `llm_plugin_claude.py` is the shortest
adapter that also wires up the MCP server, and is the better template than
the older Cline/Aider adapters if the target CLI supports fine-grained tool
permissions.
