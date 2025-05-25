from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from time import sleep
from typing import Any, Sequence

from .cli_tool import CLITool
from .log_utility import add_log_arguments, configure_logging
from .xrandr import Configuration, Mode, Position, Resolution, Screen, XRandr

logger = logging.getLogger(__name__)


@dataclass
class PACtl:
    tool: CLITool = field(default_factory=lambda: CLITool("pactl"))

    def get_sinks(self) -> list[str]:
        data: list[dict[str, str | int]] = json.loads(
            self.tool.invoke(["-f", "json", "list", "short", "sinks"])
        )
        sinks: list[str] = []
        for entry in data:
            sinks.append(str(entry["name"]))
        return sinks

    def get_default_sink(self) -> str:
        return self.tool.invoke(["get-default-sink"])

    def set_default_sink(self, name: str) -> None:
        logger.info(f"setting default PulseAudio sink to {name}")
        self.tool.invoke(["set-default-sink", name])

    def cycle_default_sink(self) -> None:
        sinks = self.get_sinks()
        default_sink = self.get_default_sink()
        selected_index = next(
            (i for i, sink in enumerate(sinks) if sink == default_sink), -1
        )
        next_sink = sinks[(selected_index + 1) % len(sinks)]
        logger.info(f"cycling PulseAudio from {default_sink} to {next_sink}")
        self.set_default_sink(next_sink)


@dataclass
class DefaultProfile:
    connected_output_names: list[str]
    profile_name: str


@dataclass
class OutputState:
    configuration: Configuration
    primary: bool


@dataclass
class Profile:
    pactl_sink_regex: str
    outputs: dict[str, OutputState]


@dataclass
class Settings:
    default_profiles: list[DefaultProfile]
    profiles: dict[str, Profile]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        return cls(
            default_profiles=[
                DefaultProfile(**default_profile)
                for default_profile in data.get("default_profiles", [])
            ],
            profiles={
                name: Profile(
                    pactl_sink_regex=profile["pactl_sink_regex"],
                    outputs={
                        output_name: OutputState(
                            configuration=Configuration(
                                mode=Mode(
                                    resolution=Resolution(
                                        **output_state["configuration"][
                                            "mode"
                                        ]["resolution"]
                                    ),
                                    refresh_rate=Decimal(
                                        output_state["configuration"]["mode"][
                                            "refresh_rate"
                                        ]
                                    ),
                                ),
                                position=Position(
                                    **output_state["configuration"]["position"]
                                ),
                            ),
                            primary=bool(output_state["primary"]),
                        )
                        for output_name, output_state in profile[
                            "outputs"
                        ].items()
                    },
                )
                for name, profile in data.get("profiles", {}).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, path: Path) -> None:
        with open(path, "w") as handle:
            json.dump(self.to_dict(), handle, indent=4)

    @classmethod
    def from_json(cls, path: Path) -> Settings:
        with open(path) as handle:
            return cls.from_dict(json.load(handle))


def gui_config_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", "")
    if not config_home:
        config_home = f"{os.environ['HOME']}/.config"
    return Path(config_home) / "ns-gui-utility"


@dataclass
class XProp:
    tool: CLITool = field(default_factory=lambda: CLITool("xprop"))

    def root_window_id(self) -> str:
        return self.tool.invoke(
            ["-root", "-notype", "_NET_WORKAREA", "_XROOTPMAP_ID"]
        )


@dataclass
class ProfileSelector:
    settings: Settings
    pactl: PACtl = field(default_factory=lambda: PACtl())
    xrandr: XRandr = field(default_factory=lambda: XRandr())
    xprop: XProp = field(default_factory=lambda: XProp())
    state: Screen = field(init=False)
    current_profile: str = field(init=False)
    default_profile: str = field(init=False)

    def __post_init__(self) -> None:
        self.update_state()

    def update_state(self) -> None:
        self.state = self.xrandr.state()
        self.current_profile = self._get_current_profile()
        self.default_profile = self._get_default_profile()

    def _get_default_profile(self) -> str:
        logger.debug("determining default profile...")
        connected_output_names = set(self.state.connected_output_names)

        best_match = max(
            (
                default_profile.profile_name
                for default_profile in self.settings.default_profiles
                if set(default_profile.connected_output_names)
                <= connected_output_names
            ),
            key=len,
            default=None,
        )
        if not best_match:
            logger.warning("could not find a matching default profile.")
            return ""
        logger.info(f"default profile determined: {best_match}")
        return best_match

    def _get_current_profile(self) -> str:
        logger.debug("determining current profile...")
        active_output_names = set(self.state.active_output_names)
        # TODO: don't just match by outputs, but also by resolution
        match = max(
            (
                name
                for name, profile in self.settings.profiles.items()
                if set(profile.outputs.keys()) == active_output_names
            ),
            key=len,
            default=None,
        )
        if not match:
            logger.warning("could not match the current profile.")
            return ""
        logger.info(f"current profile determined: {match}")
        return match

    def next_valid_profile(self, name: str = "") -> str:
        if not name:
            name = self.current_profile
        logger.debug("determining next profile...")
        profile_names = list(self.settings.profiles.keys())
        if name:
            # the order of profiles to check, ignoring the passed one
            index = profile_names.index(name)
            profile_list = profile_names[index + 1 :] + profile_names[:index]
        else:
            # all profiles
            profile_list = profile_names
        connected_output_names = set(self.state.connected_output_names)
        for next_profile in profile_list:
            profile = self.settings.profiles[next_profile]
            if set(profile.outputs.keys()) <= connected_output_names:
                logger.info(f"next valid profile determined: {next_profile}")
                return next_profile
        logger.warning("no valid next profile could be determined.")
        return ""

    def apply_profile(self, name: str) -> None:
        if self.current_profile == name:
            logger.warning(f"not reapplying current profile: {name}")
            return
        logger.info(f"applying profile: {name}")
        profile = self.settings.profiles[name]
        xrandr_cli: list[str] = []

        any_output = False
        for output in self.state.connected_output_names:
            if output in profile.outputs:
                output_state = profile.outputs[output]
                xrandr_cli.extend(
                    [
                        "--output",
                        output,
                        "--mode",
                        str(output_state.configuration.mode.resolution),
                    ]
                )
                if output_state.configuration.mode.refresh_rate:
                    xrandr_cli.extend(
                        [
                            "--rate",
                            str(output_state.configuration.mode.refresh_rate),
                        ]
                    )
                xrandr_cli.extend(
                    ["--pos", output_state.configuration.position.to_cli_str()]
                )
                if output_state.primary:
                    xrandr_cli.append("--primary")
                any_output = True
            else:
                xrandr_cli.extend(["--output", output, "--off"])
        if not any_output:
            raise AssertionError("no output would have been enabled!")

        old_root_window_id = self.xprop.root_window_id()
        logger.info("invoking xrandr...")
        self.xrandr.invoke(xrandr_cli)

        if profile.pactl_sink_regex:
            sink_pattern = re.compile(profile.pactl_sink_regex)
            matched_sinks = [
                sink
                for sink in self.pactl.get_sinks()
                if sink_pattern.match(sink)
            ]
            if matched_sinks:
                self.pactl.set_default_sink(matched_sinks[0])
            else:
                logger.warn(f"no sink matched: {profile.pactl_sink_regex}")

        on_change = gui_config_dir() / "on-output-profile-change"
        if on_change.exists():
            delay_ms = 100
            remaining_ms = 5000
            while remaining_ms > 0:
                if self.xprop.root_window_id() != old_root_window_id:
                    logger.info("new root window id detected")
                    break
                logger.debug(f"waiting {delay_ms}ms for new root window...")
                sleep(delay_ms / 1000)
                remaining_ms -= delay_ms
            if remaining_ms <= 0:
                logger.error("no new root pixmap within timeout.")

            print(CLITool(binary=str(on_change)).invoke([name]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Applies/cycles output profiles."
    )
    add_log_arguments(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-l", "--list", action="store_true", help="Lists available profiles."
    )
    group.add_argument(
        "-d",
        "--default",
        action="store_true",
        help="Apply the default profile given currently connected outputs.",
    )
    group.add_argument(
        "-c",
        "--cycle",
        action="store_true",
        help="Cycles between display profiles.",
    )
    group.add_argument(
        "-p",
        "--profile",
        type=str,
        help="Sets the profile to the specified one.",
    )
    group.add_argument(
        "-g",
        "--get-current-profile",
        action="store_true",
        help="Gets the current profile.",
    )
    group.add_argument(
        "--primary-resolution",
        action="store_true",
        help="Gets the primary output's resolution."
    )
    args = parser.parse_args(args=argv)
    configure_logging(args)

    if args.primary_resolution:
        print(XRandr().state().primary_output.configuration.mode.resolution)
    else:
        settings = Settings.from_json(gui_config_dir() / "output-profiles.json")
        selector = ProfileSelector(settings)
        if args.list:
            for profile in settings.profiles:
                print(profile)
        elif args.get_current_profile:
            print(selector.current_profile)
        else:
            if args.default:
                next_profile = selector.default_profile
            elif args.cycle:
                next_profile = selector.next_valid_profile()
            elif args.profile:
                next_profile = args.profile

            if next_profile:
                selector.apply_profile(next_profile)
            else:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
