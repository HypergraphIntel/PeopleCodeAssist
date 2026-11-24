"""Claude Code adapter for PeopleTools 8.63 Application Designer AI Assist.

Place this next to Oracle's llm_plugin_base.py in PeopleCodeAssist/Plugins.
Requires authenticated Claude Code CLI and Python 3.12+ on Windows.
Initial implementation is intentionally READ-ONLY; disable write-back in PSIDE.
PeopleSoft metadata/PeopleCode access goes through the MCPServers/ps_mcp_server.py
MCP server (MODE=read), not through file/shell tools. PSIDE spawns a brand new
plugin process per "Ask AI" turn, so that server runs as a persistent
background process (see LLMPluginBase.ensure_mcp_server_running) reused across
turns, rather than as this process's own stdio child: a fresh server per turn
would mean re-authenticating to the database on every single question.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from llm_plugin_base import LLMPluginBase, LLMPluginExitCodes

LOG = os.path.join(tempfile.gettempdir(), "llm_plugin_claude.log")
logging.basicConfig(filename=LOG, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# Don't send unbounded project content to the model.
MAX_FILE_BYTES = 150_000
MAX_TOTAL_BYTES = 750_000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".pcode", ".peoplecode", ".sql", ".json", ".xml",
    ".yaml", ".yml", ".ini", ".cfg", ".log", ".csv", ".py", ".java"
}

# PeopleSoft metadata/PeopleCode server (PSAIAPI-backed). MODE=read registers
# only the get_*_details/get_*_peoplecode tools -- no compile/stage/save --
# since write-back is not implemented by this adapter yet. Runs persistently
# on localhost (see ensure_mcp_server_running), not as this process's child.
MCP_SERVER_NAME = "psoft"
MCP_SERVER_PORT = 8933

# peoplecode-generation Skill, bundled statically since this session has no
# file/shell tool to load it on demand the way Skill.md itself describes
# (its lookup_peoplecode_reference script needs a shell tool we don't grant).
SKILL_DIR = Path(__file__).resolve().parent.parent / "Skills" / "peoplecode-generation"
SKILL_REFERENCE_FILES = [
    "references/syntax/01_core/core_rules_and_quality.md",
    "references/syntax/02_control_flow/control_flow.md",
    "references/syntax/03_functions/functions_and_declarations.md",
    "references/syntax/04_objects_and_classes/application_classes_and_imports.md",
    "references/syntax/04_objects_and_classes/class_definition_examples.md",
    "references/syntax/04_objects_and_classes/objects_properties_methods.md",
    "references/syntax/05_data_and_sql/data_buffer_rowset_patterns.md",
    "references/syntax/05_data_and_sql/sql_patterns_and_security.md",
    "references/peoplecode_built_in_functions_by_category.md",
]
SKILL_NOTICE = (
    "SKILL REFERENCE (peoplecode-generation, bundled statically below -- "
    "this session has no file or shell tool to fetch it on demand): use "
    "verified PeopleCode built-in names, signatures, and return types from "
    "the catalog below; do not invent them. The catalog lists built-ins by "
    "category and name only -- this session cannot run the "
    "lookup_peoplecode_reference script that fetches exact signatures, so "
    "if a built-in's exact signature matters and isn't already obvious "
    "from the category listing or the syntax references, say so explicitly "
    "rather than guessing."
)

TOOLS_NOTICE = (
    "SYSTEM NOTE: this session has no file or shell tool access. The only "
    f"tool available is the PeopleSoft metadata/PeopleCode server, as "
    f"mcp__{MCP_SERVER_NAME}__* tools -- use it to look up records, pages, "
    "components, application packages/classes, application engine programs, "
    "and projects, and to fetch their PeopleCode, rather than guessing from "
    "the bounded snapshot below. App Designer's exported project context for "
    "the files it flagged as relevant is included below as plain text, each "
    "file wrapped in \"--- BEGIN FILE ... --- END FILE ---\" markers. Do not "
    "attempt to list directories, read arbitrary files, or call any tool "
    "outside that MCP server -- none are available and the attempt will just "
    "be denied. This is a read-only session: the MCP server cannot compile, "
    "stage, or save changes. If the included context plus the MCP lookups "
    "are not enough to answer, say so and ask the user to open the relevant "
    "object in Application Designer, per the single-clarifying-question "
    "rule below."
)


def get_claude_executable():
    """Use the native Windows executable rather than an npm shell shim."""
    result = shutil.which("claude.exe")
    if result:
        return result
    # Winget is commonly installed per user; PATH can differ for App Designer.
    local = os.getenv("LOCALAPPDATA", "")
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates = list(packages.glob("Anthropic.ClaudeCode_*/claude.exe"))
        if candidates:
            return str(candidates[0])
    raise FileNotFoundError(
        "claude.exe not found. Install Claude Code natively or add its directory to PATH."
    )


def load_text_file(path, remaining):
    """Read one text file; do not log its contents."""
    p = Path(path)
    if not p.is_file() or p.suffix.lower() not in TEXT_EXTENSIONS:
        return "", 0
    size = p.stat().st_size
    if size > MAX_FILE_BYTES or size > remaining:
        return "", 0
    try:
        content = p.read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, UnicodeError):
        logging.warning("Unable to read context file %s", p)
        return "", 0
    return f"\n--- BEGIN FILE: {p} ---\n{content}\n--- END FILE ---\n", size


def load_skill_reference():
    """Bundle the peoplecode-generation skill's reference files as static text."""
    sections = [SKILL_NOTICE]
    for rel_path in SKILL_REFERENCE_FILES:
        path = SKILL_DIR / rel_path
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            logging.warning("Skill reference file missing: %s", path)
            continue
        sections.append(f"\n--- BEGIN SKILL REFERENCE: {rel_path} ---\n{text}\n--- END SKILL REFERENCE ---\n")
    return "\n".join(sections)


class ClaudePlugin(LLMPluginBase):
    def build_mcp_config(self):
        """Write a one-off --mcp-config file pointing at the persistent
        PeopleSoft MCP server, starting that server if it isn't already
        running (see LLMPluginBase.ensure_mcp_server_running).

        A fresh config file is written per invocation (into
        context_cache_dir, which App Designer already creates per session)
        rather than relying on any shared/committed .mcp.json, matching
        --strict-mcp-config below -- but the server it points at is the same
        long-lived one across every turn.
        """
        server_url = self.ensure_mcp_server_running(MCP_SERVER_PORT, mode="read")

        config = {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "type": "http",
                    "url": server_url,
                }
            }
        }
        config_path = self.context_dir / "mcp_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        logging.info("Wrote MCP config for server '%s' (%s) to %s", MCP_SERVER_NAME, server_url, config_path)
        return config_path

    def build_command(self):
        a = self.plugin_attributes
        if "enable_write" in a:
            # TODO(write-mode): before implementing this, confirm how PSIDE
            # actually persists a write-enabled change once the plugin exits:
            # (a) file roundtrip -- PSIDE re-reads write_enabled_files from
            #     disk and compiles/saves them itself, same as Cline/Aider
            #     (WRITE_MODE_AGENTS.md's "only modify existing files" rule
            #     points this way) -- in which case Claude needs a plain
            #     Edit/apply-patch capability scoped to just those files, not
            #     the MCP save tools; or
            # (b) MCP stage/save -- the plugin itself must push the change via
            #     ps_mcp_server.py's stage_*_peoplecode + save_all_peoplecode
            #     (UpdateMultiPeopleCodeData), independent of the local files.
            # Picking wrong means write-mode silently no-ops or double-applies.
            # No shipped doc under PS_HOME settles this (checked); the
            # empirical test is: enable write-back, run the existing Cline
            # adapter on a trivial change, and see whether the DB object
            # updates with no further action once write_enabled_files changed
            # on disk. See memory `peoplecodeassist-write-mode-question`.
            raise ValueError(
                "Write-back is not supported by this read-only Claude adapter. "
                "Uncheck Enable write-back mode in Application Designer."
            )

        self.context_dir = Path(a["context_cache_dir"]).resolve()
        if not self.context_dir.is_dir():
            raise FileNotFoundError(f"Project context directory not found: {self.context_dir}")
        prompt_file = Path(a["prompt_file"]).resolve()
        user_prompt = prompt_file.read_text(encoding="utf-8-sig", errors="replace")
        agents = self.context_dir / "AGENTS.md"
        rule_text = agents.read_text(encoding="utf-8-sig", errors="replace")

        mcp_config_path = self.build_mcp_config()

        # --tools '' disables the CLI's built-in file/edit/shell tools. Only
        # the files App Designer explicitly flagged as relevant go in as a
        # bounded textual snapshot; anything else about the project is
        # reachable on demand through the mcp__psoft__* tools instead of
        # being guessed at and stuffed in here.
        budget = MAX_TOTAL_BYTES
        fragments = []
        seen = {prompt_file, agents}
        explicit = a.get("read_only_files", []) + a.get("write_enabled_files", [])
        if not isinstance(explicit, list):
            explicit = []
        for filename in explicit:
            path = Path(filename).resolve()
            if path in seen:
                continue
            seen.add(path)
            content, used = load_text_file(path, budget)
            fragments.append(content)
            budget -= used

        sections = [TOOLS_NOTICE, rule_text.strip(), load_skill_reference(), user_prompt.strip()]
        sections.extend(fragment for fragment in fragments if fragment)
        self.input_text = "\n\n".join(sections) + "\n"

        self.llm_client_cli_command = [
            get_claude_executable(),
            "--print",
            "--tools", "",
            # --restricted also skips user/project/local settings (custom
            # commands, hooks, etc.) and refuses bypassPermissions.
            "--restricted",
            # Only the server from --mcp-config loads; no .mcp.json, no
            # user/project-configured MCP servers.
            "--strict-mcp-config",
            "--mcp-config", str(mcp_config_path),
            # Auto-approve calls to that one server's tools; --permission-prompts
            # none below denies everything else that would otherwise prompt.
            "--allowedTools", f"mcp__{MCP_SERVER_NAME}__*",
            "--permission-prompts", "none",
        ]

    def execute(self, named_pipe):
        logging.info("Inside execute")
        with open(named_pipe, "wb", buffering=0) as pipe:
            logging.info("Successfully opened the named pipe: %s", named_pipe)
            logging.info("Going to run the LLM CLI command")
            try:
                process = subprocess.Popen(
                    self.llm_client_cli_command,
                    cwd=self.context_dir,
                    stdin=subprocess.PIPE,
                    stdout=pipe,
                    stderr=pipe,
                    bufsize=0,
                )
            except OSError as e:
                logging.error("Error while running LLM CLI command: %s", e)
                pipe.write(f"{e}\n".encode("utf-8"))
                return LLMPluginExitCodes.ERROR_LLM_CLIENT_INVOCATION

            process.communicate(input=self.input_text.encode("utf-8"))
            logging.info("LLM CLI command completed")
            return LLMPluginExitCodes.SUCCESS


def main():
    parser = argparse.ArgumentParser(description="Claude Code LLM Client Adapter")
    parser.add_argument("--pipe", required=True, help="The named pipe from C++ server")
    args, remaining = parser.parse_known_args()
    llm_plugin_attributes = " ".join(remaining)

    try:
        plugin = ClaudePlugin()
        logging.info("Initializing plugin attributes")
        plugin.initialize_plugin_attributes(llm_plugin_attributes)
        logging.info("Plugin attributes keys: %s", sorted(plugin.plugin_attributes))

        logging.info("Generating AGENTS.md in context cache from system and user rules")
        success, agents_file_result = plugin.generate_agents_file_in_workdir()
        if not success:
            logging.error("Failed generating AGENTS.md: %s", agents_file_result)
            sys.exit(LLMPluginExitCodes.ERROR_SCRIPT_FAILURE)
        logging.info("Generated AGENTS.md at: %s", agents_file_result)

        logging.info("Building LLM CLI command line")
        plugin.build_command()
        logging.info("LLM CLI command line is: %s", plugin.llm_client_cli_command)

        exit_code = plugin.execute(args.pipe)
        sys.exit(exit_code)
    except (FileNotFoundError, OSError):
        logging.exception("Could not find pipe %s. Ensure PSIDE is running.", args.pipe)
        sys.exit(LLMPluginExitCodes.ERROR_PIPE_NOT_AVAILABLE)
    except Exception:
        logging.exception("General exception")
        sys.exit(LLMPluginExitCodes.ERROR_SCRIPT_FAILURE)


if __name__ == "__main__":
    main()