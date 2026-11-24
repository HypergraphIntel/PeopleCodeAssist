"""Codex CLI adapter for PeopleTools 8.63 Application Designer AI Assist.

Place this next to Oracle's llm_plugin_base.py in PeopleCodeAssist/Plugins.
Requires authenticated OpenAI Codex CLI on PATH and Python 3.12+ on Windows.
Initial implementation is intentionally READ-ONLY; disable write-back in PSIDE.
PeopleSoft metadata/PeopleCode access goes through the MCPServers/ps_mcp_server.py
MCP server (MODE=read), not through file/shell tools.

Mirrors llm_plugin_claude.py's shape and MCP hookup; see that file for the
rationale behind the bounded-context + MCP-tool split. Codex CLI has no
--mcp-config/--allowedTools equivalent, so the server is wired up with
per-invocation `-c key=value` config overrides instead of a JSON file, and
the prompt is piped via stdin using Codex's `-` sentinel rather than a
positional argument.
"""

import argparse
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from llm_plugin_base import LLMPluginBase, LLMPluginExitCodes

LOG = os.path.join(tempfile.gettempdir(), "llm_plugin_codex.log")
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
# since write-back is not implemented by this adapter yet.
MCP_SERVER_NAME = "psoft"
MCP_SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "MCPServers" / "ps_mcp_server.py"

TOOLS_NOTICE = (
    "SYSTEM NOTE: this session has no file or shell tool access. The only "
    f"tool available is the PeopleSoft metadata/PeopleCode server, as "
    f"mcp_servers.{MCP_SERVER_NAME} tools -- use it to look up records, pages, "
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


def get_codex_executable():
    """Use the native Windows executable rather than an npm shell shim."""
    result = shutil.which("codex.exe") or shutil.which("codex")
    if result:
        return result
    raise FileNotFoundError(
        "codex(.exe) not found. Install the Codex CLI natively or add its directory to PATH."
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


def _toml_literal(value):
    """Render a string as a TOML literal string (single-quoted, no escaping).

    Used for -c overrides carrying Windows paths: literal strings pass
    backslashes through untouched, unlike TOML basic (double-quoted) strings.
    Values must not themselves contain a single quote (not the case for any
    path or mode string used here).
    """
    if "'" in value:
        raise ValueError(f"Cannot render as a TOML literal string: {value!r}")
    return f"'{value}'"


class CodexPlugin(LLMPluginBase):
    def mcp_config_overrides(self):
        """Build the -c key=value pairs wiring up the PeopleSoft MCP server.

        Codex CLI has no one-off --mcp-config file flag; config.toml entries
        are set per invocation with `-c dotted.key=value` overrides instead,
        same as everything else below that would otherwise live in config.toml.
        """
        if not MCP_SERVER_SCRIPT.is_file():
            raise FileNotFoundError(f"MCP server script not found: {MCP_SERVER_SCRIPT}")

        prefix = f"mcp_servers.{MCP_SERVER_NAME}"
        overrides = [
            f"{prefix}.command={_toml_literal(sys.executable)}",
            f"{prefix}.args=[{_toml_literal(str(MCP_SERVER_SCRIPT))}]",
            f"{prefix}.env.MODE={_toml_literal('read')}",
            f"{prefix}.startup_timeout_sec=20",
        ]
        args = []
        for override in overrides:
            args.extend(["-c", override])
        return args

    def build_command(self):
        a = self.plugin_attributes
        if "enable_write" in a:
            raise ValueError(
                "Write-back is not supported by this read-only Codex adapter. "
                "Uncheck Enable write-back mode in Application Designer."
            )

        self.context_dir = Path(a["context_cache_dir"]).resolve()
        if not self.context_dir.is_dir():
            raise FileNotFoundError(f"Project context directory not found: {self.context_dir}")
        prompt_file = Path(a["prompt_file"]).resolve()
        user_prompt = prompt_file.read_text(encoding="utf-8-sig", errors="replace")
        agents = self.context_dir / "AGENTS.md"
        rule_text = agents.read_text(encoding="utf-8-sig", errors="replace")

        mcp_overrides = self.mcp_config_overrides()

        # Only the files App Designer explicitly flagged as relevant go in as
        # a bounded textual snapshot; anything else about the project is
        # reachable on demand through the mcp_servers.psoft tools instead of
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

        sections = [TOOLS_NOTICE, rule_text.strip(), user_prompt.strip()]
        sections.extend(fragment for fragment in fragments if fragment)
        self.input_text = "\n\n".join(sections) + "\n"

        self.llm_client_cli_command = [
            get_codex_executable(),
            "exec",
            # Read the prompt from stdin rather than a positional argument.
            "-",
            # context_cache_dir is a plain App Designer scratch folder, not a
            # git repo; Codex otherwise refuses to run outside one.
            "--skip-git-repo-check",
            # No file/shell escalation of any kind: the bounded snapshot
            # above plus the MCP server below are the only project context.
            "--sandbox", "read-only",
            # Non-interactive: nobody is available to answer an approval
            # prompt, so none should ever be raised.
            "-c", "approval_policy='never'",
            *mcp_overrides,
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
    parser = argparse.ArgumentParser(description="Codex CLI LLM Client Adapter")
    parser.add_argument("--pipe", required=True, help="The named pipe from C++ server")
    args, remaining = parser.parse_known_args()
    llm_plugin_attributes = " ".join(remaining)

    try:
        plugin = CodexPlugin()
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
