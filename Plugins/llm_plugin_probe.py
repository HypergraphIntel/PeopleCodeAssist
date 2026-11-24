"""Minimal PeopleTools 8.63 Application Designer AI Assist protocol probe.

Does not run Claude or change project files. It only generates Oracle's AGENTS.md
(as the delivered sample does) and sends a diagnostic message to the named pipe.
"""
import argparse
import logging
import os
import sys
import tempfile

from llm_plugin_base import LLMPluginBase, LLMPluginExitCodes

logging.basicConfig(
    filename=os.path.join(tempfile.gettempdir(), "llm_plugin_probe.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)


class ProbePlugin(LLMPluginBase):
    def build_command(self):
        self.llm_client_cli_command = ["diagnostic-only; no subprocess"]

    def execute(self, named_pipe):
        try:
            logging.info("Opening named pipe")
            with open(named_pipe, "wb", buffering=0) as pipe:
                pipe.write(b"PeopleCode Assist plugin named-pipe probe succeeded. Claude was not invoked.\r\n")
            logging.info("Named-pipe test succeeded")
            return LLMPluginExitCodes.SUCCESS
        except OSError:
            logging.exception("Named-pipe test failed")
            return LLMPluginExitCodes.ERROR_PIPE_NOT_AVAILABLE


def main():
    parser = argparse.ArgumentParser(description="PeopleCode Assist plugin protocol probe")
    parser.add_argument("--pipe", required=True)
    args, remaining = parser.parse_known_args()
    logging.info("Probe started; arguments received")
    try:
        plugin = ProbePlugin()
        plugin.initialize_plugin_attributes(" ".join(remaining))
        logging.info("Attributes parsed; keys: %s", sorted(plugin.plugin_attributes))
        ok, result = plugin.generate_agents_file_in_workdir()
        if not ok:
            logging.error("Oracle AGENTS.md generation failed: %s", result)
            return LLMPluginExitCodes.ERROR_SCRIPT_FAILURE
        plugin.build_command()
        return plugin.execute(args.pipe)
    except Exception:
        logging.exception("Probe failed before pipe execution")
        return LLMPluginExitCodes.ERROR_SCRIPT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
