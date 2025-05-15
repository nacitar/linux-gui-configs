from __future__ import annotations

import argparse
import logging
from typing import Sequence

from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Uses dbus to control spotify."
    )
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)
    print("DO STUFF")
    import os

    print(os.environ["PWD"])
    return 0
