from __future__ import annotations

import sys
from typing import Callable, Sequence

from . import output_profile, spotify_control


def main(argv: Sequence[str] | None = None) -> int:
    main_lookup: dict[str, Callable[[Sequence[str] | None], int]] = {
        "output-profile": output_profile.main,
        "spotify-control": spotify_control.main,
    }
    if len(sys.argv) > 1:
        command = sys.argv[1]
        del sys.argv[1]
        command_main = main_lookup.get(command, None)
        if command_main:
            return command_main(argv)

    raise ValueError(
        "must invoke as or specify a valid subcommand:"
        f" {', '.join(main_lookup.keys())}"
    )
