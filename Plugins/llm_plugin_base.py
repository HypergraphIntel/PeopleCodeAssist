import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


class LLMPluginExitCodes:
    """Class containing error codes exposed by PeopleSoft App Designer for LLM plugin execution"""
    SUCCESS = 0
    ERROR_PIPE_NOT_AVAILABLE = 16001  # Named Pipe is not available
    ERROR_LLM_CLIENT_INVOCATION = 16002  # LLM Client invocation failed
    ERROR_SCRIPT_FAILURE = 16003  # LLM Plugin script failed


class LLMPluginBase:
    """Base class for all Windows CLI based LLM plugins"""
    MAX_USER_RULES_LENGTH = 1000

    def __init__(self):
        self.plugin_attributes = {}
        self.llm_client_cli_command = None

    def initialize_plugin_attributes(self, plugin_attributes):
        plugin_attributes_refined = re.sub(r',(?=\s*?[]})])', '', plugin_attributes)
        self.plugin_attributes = json.loads(plugin_attributes_refined)

    def build_command(self):
        """Build command line arguments for the LLM client CLI"""
        raise NotImplementedError

    def execute(self, named_pipe):
        """Execute LLM client CLI command"""
        raise NotImplementedError

    def generate_agents_file_in_workdir(self):
        """Create AGENTS.md in context cache by combining system and user rules."""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(plugin_dir)
        system_agents_filename = "WRITE_MODE_AGENTS.md" if "enable_write" in self.plugin_attributes else "READ_MODE_AGENTS.md"
        system_agents_file = os.path.join(root_dir, "SystemPrompts", system_agents_filename)
        rules_file = os.path.join(root_dir, "UserRules", "RULES.md")

        try:
            if not os.path.isfile(system_agents_file):
                raise FileNotFoundError(f"SystemPrompts\\{system_agents_filename} source path not found: {system_agents_file}")
            if not os.path.isfile(rules_file):
                raise FileNotFoundError(f"UserRules\\RULES.md source path not found: {rules_file}")

            context_cache_dir = self.plugin_attributes.get("context_cache_dir")
            if not context_cache_dir:
                raise KeyError("Missing required plugin attribute: context_cache_dir")

            target_file = os.path.join(context_cache_dir, "AGENTS.md")

            with open(system_agents_file, "r", encoding="utf-8") as system_file:
                system_agents_content = system_file.read()

            with open(rules_file, "r", encoding="utf-8") as user_rules_file:
                user_rules_content = user_rules_file.read()

            if len(user_rules_content) > self.MAX_USER_RULES_LENGTH:
                raise ValueError(f"UserRules\\RULES.md exceeds max allowed length of {self.MAX_USER_RULES_LENGTH} characters")

            combined_content = system_agents_content.rstrip()
            if user_rules_content.strip():
                combined_content += (
                    "\n\n## User Rules\n"
                    f"{user_rules_content.strip()}\n"
                )
            else:
                combined_content += "\n"

            with open(target_file, "w", encoding="utf-8") as output_file:
                output_file.write(combined_content)

            return True, target_file
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _mcp_port_open(port, timeout=0.3):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def ensure_mcp_server_running(self, port, mode="read", startup_timeout=15.0):
        """Make sure the shared PeopleSoft MCP server (MCPServers/ps_mcp_server.py)
        is listening on `port`, starting it as a detached background process
        if nothing answers there yet, and return its base URL.

        Every "Ask AI" turn spawns a brand-new plugin (and LLM CLI) process,
        but the MCP server stays up across turns once started here: tool
        calls in a later turn reuse the same already-authenticated session
        instead of re-running the sign-on dialog from scratch each time.
        Started detached (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP on
        Windows) so it outlives this short-lived launcher process; it exits
        itself after IDLE_SHUTDOWN_SECONDS of inactivity (see ps_mcp_server.py).
        """
        if not self._mcp_port_open(port):
            script = Path(__file__).resolve().parent.parent / "MCPServers" / "ps_mcp_server.py"
            if not script.is_file():
                raise FileNotFoundError(f"MCP server script not found: {script}")

            env = os.environ.copy()
            env["TRANSPORT"] = "streamable-http"
            env["PORT"] = str(port)
            env["MODE"] = mode

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(
                [sys.executable, str(script)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )

            deadline = time.time() + startup_timeout
            while time.time() < deadline:
                if self._mcp_port_open(port):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(
                    f"PeopleSoft MCP server did not start listening on port {port} "
                    f"within {startup_timeout}s"
                )

        return f"http://127.0.0.1:{port}/mcp"
