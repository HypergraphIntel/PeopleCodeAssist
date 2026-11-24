import subprocess
import sys
import argparse
import logging
import os
import tempfile

from llm_plugin_base import LLMPluginExitCodes, LLMPluginBase

# Logging Configuration
# By default logging is disabled. Set ENABLE_LOGGING to True to enable logging
ENABLE_LOGGING = False

if ENABLE_LOGGING:
    # Log file will be generated on the users Temp directory with name same as the plugin script
    # Ex: C:\Users\<User>\AppData\Local\Temp\llm_plugin_aider.log
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
class AiderPlugin(LLMPluginBase):
    """Plugin for invoking CLI command for LLM Client Aider"""

    def build_command(self):
        self.llm_client_cli_command = ["aider"]

        # Add no git flag
        self.llm_client_cli_command.append("--no-git")

        # Append guideline notes to beginning of message file to tell the LLM client not to modify the files in
        # the project context folder in case of read only context
        # For write enabled context, allow edits to files in the project context folder
        guidelines_notes_read_only_context = [
            "GENERAL GUIDELINES TO THE MODEL:",
            "- Apply all edits and changes only to llm_response.txt file. Do not edit any other file",
            "- Before executing any user-requested work, you must read the chat history file llm_chat_session_history.txt if available to understand previous conversations",
            " ",
            "Given below is the query to the model:"
        ]


        guidelines_notes_write_enabled_context = [
            "GENERAL GUIDELINES TO THE MODEL:",
            "- Do not add new files",
            "- Allow edits to files in the project context directory",
            "- Edit source code files in the project context directory only for coding tasks",
            "- Before executing any user-requested work, you must read the chat history file llm_chat_session_history.txt if available to understand previous conversations",
            " ",
            "Given below is the query to the model:"
        ]

        with open(self.plugin_attributes["prompt_file"], 'r') as prompt_file:
            prompt_file_content = prompt_file.read()

        with open(self.plugin_attributes["prompt_file"], 'w') as prompt_file:
            if "enable_write" in self.plugin_attributes:
                prompt_file.writelines([line + '\n' for line in guidelines_notes_write_enabled_context])
            else:
                prompt_file.writelines([line + '\n' for line in guidelines_notes_read_only_context])
            prompt_file.write(prompt_file_content)

        # Add file containing the prompt
        self.llm_client_cli_command.extend(["--message-file", self.plugin_attributes["prompt_file"]])

        # Add context cache
        if "enable_write" not in self.plugin_attributes:
            self.llm_client_cli_command.extend(["--read", self.plugin_attributes["context_cache_dir"]])

        # Add all read only files
        if "read_only_files" in self.plugin_attributes:
            read_only_files_list = self.plugin_attributes['read_only_files']
            for read_only_file in read_only_files_list:
                self.llm_client_cli_command.extend(["--read", read_only_file])

        # Add all write enabled files
        if "write_enabled_files" in self.plugin_attributes:
            write_enabled_files_list = self.plugin_attributes['write_enabled_files']
            for write_enabled_file in write_enabled_files_list:
                self.llm_client_cli_command.extend(["--file", write_enabled_file])

    def execute(self, named_pipe):
        logging.info("Inside execute")
        with open(named_pipe, 'wb', buffering=0) as pipe:
            logging.info("Successfully opened the named pipe: " + named_pipe)

            logging.info("Going to run the LLM CLI command")
            try:
                process = subprocess.Popen(
                    self.llm_client_cli_command,
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


def main():
    logging.info("Inside Aider LLM Client Adapter")
    parser = argparse.ArgumentParser(description="Aider LLM Client Adapter")
    parser.add_argument('--pipe', required=True, help='The named pipe from C++ server')
    args, remaining = parser.parse_known_args()
    llm_plugin_attributes = ' '.join(remaining)

    try:
        plugin = AiderPlugin()

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
