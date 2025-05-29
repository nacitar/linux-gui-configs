import logging
import os
import shlex
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CLI_ENV = os.environ.copy()
_CLI_ENV["LC_ALL"] = "C"


@dataclass
class CLITool:
    binary: str

    def invoke(
        self, arguments: list[str], *, capture_output: bool = True
    ) -> str:
        command_line = [self.binary] + arguments
        str_command_line = " ".join(shlex.quote(arg) for arg in command_line)
        logger.info(f"Invoking: {str_command_line}")
        result = subprocess.run(
            command_line,
            capture_output=capture_output,
            text=True,
            env=_CLI_ENV,
        )
        if result.returncode:
            error = (
                result.stderr
                if capture_output
                else f"exited with code {result.returncode}"
            )
            logger.error(error)
            raise RuntimeError(error)
        if capture_output:
            return result.stdout.strip()
        return ""
