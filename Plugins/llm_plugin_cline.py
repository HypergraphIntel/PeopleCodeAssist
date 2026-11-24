import subprocess
import sys
import argparse
import logging
import os
import tempfile
import shutil
from pathlib import Path

from llm_plugin_base import LLMPluginExitCodes, LLMPluginBase

# Logging Configuration
# By default logging is disabled. Set ENABLE_LOGGING to True to enable logging
ENABLE_LOGGING = False

if ENABLE_LOGGING:
    # Log file will be generated on the users Temp directory with name same as the plugin script
    # Ex: C:\Users\<User>\AppData\Local\Temp\llm_plugin_ocaider.log
    llm_plugin_log_file_name = os.path.basename(sys.argv[0]).replace('.py', '.log')
    llm_plugin_log_path = os.path.join(tempfile.gettempdir(), llm_plugin_log_file_name)

    logging.basicConfig(
        filename=llm_plugin_log_path,
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
else:
    logging.disable(logging.CRITICAL)


# LLM Plugin implementation
class ClineCLIPlugin(LLMPluginBase):
    """Plugin for invoking LLM Client Cline CLI"""
    
    # -------------------------------------------------------------------------
    # Optional CLI Settings
    # -------------------------------------------------------------------------

    # Enable enhanced reasoning/thinking mode.
    #
    # Default: False. Change to True to enable reasoning/thinking mode
    #
    # When enabled, Cline will pass the '--thinking' parameter to the model.
    # This may improve the quality of responses for complex analysis, code
    # review, refactoring, troubleshooting, and design-related prompts.
    #
    # Notes:
    # - Some models do not support thinking mode.
    # - Some providers may ignore this setting when unsupported.
    # - Some providers may return an error if thinking mode is enabled for an
    #   unsupported model.
    #
    ENABLE_THINKING = False

    # Thinking level to use when ENABLE_THINKING is True.
    #
    # Supported values:
    #   "low"
    #   "medium"
    #   "high"
    #   "xhigh"
    #
    THINKING_LEVEL = "high"
    

    def build_command(self):
        # Specify cline full path
        cline_full_path = shutil.which("cline")

        if not cline_full_path:
            raise FileNotFoundError("cline not found in PATH")

        # Windows fix: ensure .cmd is used
        if not cline_full_path.lower().endswith(".cmd"):
            cline_cmd = cline_full_path + ".cmd"
            if os.path.exists(cline_cmd):
                cline_full_path = cline_cmd

        self.llm_client_cli_command = [cline_full_path]

        # Specify context cache
        self.llm_client_cli_command.extend(["--cwd", self.plugin_attributes["context_cache_dir"]])
        
        # Specify thinking level for the LLM if enabled
        if self.ENABLE_THINKING:
            self.llm_client_cli_command.extend(["--thinking", self.THINKING_LEVEL])

        # Specify prompt
        prompt_file_path = self.plugin_attributes["prompt_file"]
        with open(prompt_file_path, 'r') as prompt_file:
            prompt_file_content = prompt_file.read()
            # Cline recognizes positional prompts by whitespace, even when subprocess passes the entire prompt as a single argument.
            if prompt_file_content and not any(char.isspace() for char in prompt_file_content):
                prompt_file_content += " "
            self.llm_client_cli_command.extend(["--yolo", prompt_file_content])

    def execute(self, named_pipe):
        logging.info("Inside execute")
    
        with open(named_pipe, 'wb', buffering=0) as pipe:
            logging.info("Successfully opened the named pipe: " + named_pipe)

            logging.info("Going to run the LLM CLI command")
            try:
                process = subprocess.Popen(
                    self.llm_client_cli_command,
                    shell=False,
                    stdout=pipe,
                    stderr=pipe,
                    bufsize=0,
                    universal_newlines=False
                )
            except OSError as e:
                logging.error(f"Error while running LLM CLI command : {str(e)}")
                pipe.write(f"{e}\n".encode("utf-8"))
                return LLMPluginExitCodes.ERROR_LLM_CLIENT_INVOCATION

            process.wait()
            logging.info("LLM CLI command completed")
            return LLMPluginExitCodes.SUCCESS

    def generate_cline_rules_file(self):
        """We need to create a .clinerules file inside the project context directory to specify rules applicable
        to all prompts as well as to specify external files (outside of project context) added to the chat session"""
        context_cache_dir = self.plugin_attributes["context_cache_dir"]
        pside_llm_agent_dir = Path(context_cache_dir).parent
        pside_llm_chat_session_history_file = os.path.join(pside_llm_agent_dir, "llm_chat_session_history.txt")
        
        mandatory_system_workflow = "# Non-negotiable execution constraints\n"
        mandatory_system_workflow += "\n"
        mandatory_system_workflow += "1. Do not create new files in the current working directory\n"
        mandatory_system_workflow += "2. Before executing any user-requested work, you must read the chat history file located at " + pside_llm_chat_session_history_file + " to understand previous conversations.\n"
        mandatory_system_workflow += "3. Always provide the substantive answer as a normal assistant response before calling any completion or submission tool.\n"
        mandatory_system_workflow += "4. For every task that generates or modifies PeopleCode, include the complete generated or modified PeopleCode in the normal assistant response before calling any completion or submission tool.\n"
        
        if "enable_write" not in self.plugin_attributes:
            mandatory_system_workflow += "5. Do not edit any files in the current working directory\n"
        
        cline_rule_content = mandatory_system_workflow
        cline_rule_content += "\n"
        
        file_extension_and_rules = "# File classification: filename-only rules\n"
        file_extension_and_rules += "\n"
        file_extension_and_rules += "Classify files by their basename only.\n"
        file_extension_and_rules += "`basename` means the final filename after the last `/` or `\`.\n"
        file_extension_and_rules += "Do not use file contents to decide whether a file is PeopleCode source.\n"
        file_extension_and_rules += "\n"
        file_extension_and_rules += "Rules:\n"
        
        file_extension_and_rules += "1. If the basename ends with `.txt` and contains the exact substring `_METHOD_`, classify it as PeopleCode source file.\n"
        file_extension_and_rules += "2. If the basename ends with `.txt` and does not contain the exact substring `_METHOD_`, classify it as a PeopleSoft object metadata file.\n"
        if "enable_write" in self.plugin_attributes:
            file_extension_and_rules += "4. When writing edits to a file, preserve the original `.txt` extension exactly as it was opened.\n"
        cline_rule_content += file_extension_and_rules
        
        cline_rule_content += "\n"

        read_only_files = self.plugin_attributes.get('read_only_files', [])
        if read_only_files:
            cline_rule_content += ("# Files listed below are read only and cannot be modified \n")
            cline_rule_content += "\n".join([f"- {file_path}" for file_path in read_only_files])
        cline_rules_file = os.path.join(self.plugin_attributes["context_cache_dir"], ".clinerules")
        with open(cline_rules_file, "w", encoding="utf-8") as rules_file:
            rules_file.write(cline_rule_content)


def main():
    logging.info("Inside Cline CLI LLM Client Adapter")
    parser = argparse.ArgumentParser(description="Cline CLI LLM Client Adapter")
    parser.add_argument('--pipe', required=True, help='The named pipe from C++ server')
    args, remaining = parser.parse_known_args()
    llm_plugin_attributes = ' '.join(remaining)

    try:
        plugin = ClineCLIPlugin()

        logging.info("Initializing plugin attributes")
        plugin.initialize_plugin_attributes(llm_plugin_attributes)
        logging.info("Plugin attributes are: " + str(plugin.plugin_attributes))

        logging.info("Generating AGENTS.md in context cache from system and user rules")
        success, agents_file_result = plugin.generate_agents_file_in_workdir()
        if not success:
            logging.error("Failed generating AGENTS.md: " + agents_file_result)
            sys.exit(LLMPluginExitCodes.ERROR_SCRIPT_FAILURE)
        logging.info("Generated AGENTS.md at: " + agents_file_result)

        logging.info("Building LLM CLI command line")
        plugin.build_command()
        logging.info("LLM CLI command line is: " + str(plugin.llm_client_cli_command))

        logging.info("Generating Cline rules file")
        plugin.generate_cline_rules_file()

        exit_code = plugin.execute(args.pipe)
        sys.exit(exit_code)
    except (FileNotFoundError, OSError):
        logging.info(f"Error: Could not find pipe {args.pipe}. Ensure if PSIDE is running.")
        sys.exit(LLMPluginExitCodes.ERROR_PIPE_NOT_AVAILABLE)
    except Exception as e:
        logging.info(f"Error: General Exception {str(e)}")
        sys.exit(LLMPluginExitCodes.ERROR_SCRIPT_FAILURE)


if __name__ == "__main__":
    main()
