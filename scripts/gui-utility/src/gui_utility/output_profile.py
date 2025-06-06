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
from .pactl import PACtl, Sink
from .xrandr import Configuration, Mode, Position, Resolution, Screen, XRandr

logger = logging.getLogger(__name__)


@dataclass
class DefaultProfile:
    connected_output_names: list[str]
    profile_name: str


@dataclass
class OutputState:
    configuration: Configuration
    primary_candidate: bool


@dataclass
class Profile:
    pactl_sink_option_regexes: list[str]
    outputs: dict[str, OutputState]

    @property  # can't cache; not frozen
    def primary_output_name(self) -> str:
        if self.outputs:
            for name, state in self.outputs.items():
                if state.primary_candidate:
                    return name
            return next(iter(self.outputs.keys()), "")
        return ""


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
                    pactl_sink_option_regexes=profile[
                        "pactl_sink_option_regexes"
                    ],
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
                            primary_candidate=bool(
                                output_state.get("primary_candidate", False)
                            ),
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


def on_primary_output_change(old: str, new: str) -> bool:
    script = gui_config_dir() / "on-primary-output-change"
    if old != new and script.exists():
        CLITool(binary=str(script)).invoke([new], capture_output=False)
        return True
    return False


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
    screen: Screen = field(init=False)
    current_profile: str = field(init=False)
    default_profile: str = field(init=False)
    candidate_pactl_sinks: list[Sink] = field(init=False)

    def __post_init__(self) -> None:
        self.update_state()

    def update_state(self) -> None:
        self.screen = self.xrandr.screen()
        self.current_profile = self._get_current_profile()
        self.default_profile = self._get_default_profile()
        self.candidate_pactl_sinks = self._get_candidate_pactl_sinks()

    def _get_default_profile(self) -> str:
        logger.debug("determining default profile...")
        connected_output_names = set(self.screen.connected_output_names)

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
        active_output_names = set(self.screen.active_output_names)
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

    def _get_candidate_pactl_sinks(self) -> list[Sink]:
        candidate_sinks = self.pactl.get_sinks()
        if self.current_profile:
            profile = self.settings.profiles[self.current_profile]
            if profile.pactl_sink_option_regexes:
                remaining_sinks: set[Sink] = set(candidate_sinks)
                filtered_sinks: list[Sink] = []
                for pactl_sink_regex in profile.pactl_sink_option_regexes:
                    pattern = re.compile(pactl_sink_regex)
                    for sink in remaining_sinks:
                        if pattern.match(sink.name):
                            remaining_sinks.remove(sink)
                            filtered_sinks.append(sink)
                            break
                if filtered_sinks:
                    candidate_sinks = filtered_sinks
        return candidate_sinks

    def cycle_pactl_sink(self) -> str:
        current_sink_name = self.pactl.get_default_sink_name()
        sink_names = [sink.name for sink in self.candidate_pactl_sinks]
        try:
            index = sink_names.index(current_sink_name)
            sink_names = sink_names[index + 1 :] + sink_names[:index]
        except ValueError:
            pass
        if sink_names:
            self.pactl.set_default_sink(sink_names[0])
            return sink_names[0]
        logger.warning("no valid next pactl sink could be determined.")
        return ""

    def cycle_primary_output(self) -> str:
        candidate_outputs: list[str] = []
        if self.current_profile:
            profile = self.settings.profiles[self.current_profile]
            candidate_outputs = [
                name
                for name, state in profile.outputs.items()
                if state.primary_candidate
            ]
        if not candidate_outputs:
            candidate_outputs = list(self.screen.active_output_names)
        primary_output_name = self.screen.primary_output.name
        next_primary_output_name = candidate_outputs[
            (candidate_outputs.index(primary_output_name) + 1)
            % len(candidate_outputs)
        ]
        self.xrandr.set_primary_output(next_primary_output_name)
        on_primary_output_change(primary_output_name, next_primary_output_name)
        return next_primary_output_name

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
        connected_output_names = set(self.screen.connected_output_names)
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

        primary_output_name = self.screen.primary_output.name
        new_primary_output_name = profile.primary_output_name
        any_output = False
        for output_name in self.screen.connected_output_names:
            if output_name in profile.outputs:
                output_state = profile.outputs[output_name]
                xrandr_cli.extend(
                    [
                        "--output",
                        output_name,
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
                if output_name == new_primary_output_name:
                    xrandr_cli.append("--primary")
                any_output = True
            else:
                xrandr_cli.extend(["--output", output_name, "--off"])
        if not any_output:
            raise AssertionError("no output would have been enabled!")

        old_root_window_id = self.xprop.root_window_id()
        logger.info("invoking xrandr...")
        self.xrandr.invoke(xrandr_cli)

        if profile.pactl_sink_option_regexes:
            for pactl_sink_regex in profile.pactl_sink_option_regexes:
                sink_pattern = re.compile(pactl_sink_regex)
                matched_sinks = [
                    sink
                    for sink in self.pactl.get_sinks()
                    if sink_pattern.match(sink.name)
                ]
                if matched_sinks:
                    logger.info(f"matches sink: {matched_sinks[0].name}")
                    self.pactl.set_default_sink(matched_sinks[0])
                    break
                else:
                    logger.warn(f"no sink matched: {pactl_sink_regex}")

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

            CLITool(binary=str(on_change)).invoke([name], capture_output=False)
        on_primary_output_change(primary_output_name, new_primary_output_name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Applies/cycles output profiles."
    )
    add_log_arguments(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--state",
        action="store_true",
        help="Prints the screen state as known by the tool.",
    )
    group.add_argument(
        "--list", action="store_true", help="Lists available profiles."
    )
    group.add_argument(
        "--default-profile",
        action="store_true",
        help="Apply the default profile given currently connected outputs.",
    )
    group.add_argument(
        "--cycle-profile",
        action="store_true",
        help="Cycles between display profiles.",
    )
    group.add_argument(
        "--cycle-primary",
        action="store_true",
        help="Cycles the current primary output among active outputs.",
    )
    group.add_argument(
        "--cycle-pactl-sink",
        action="store_true",
        help=(
            "Cycles the pactl sink among candidates in the profile,"
            " or all if none are present."
        ),
    )
    group.add_argument(
        "--profile", type=str, help="Sets the profile to the specified one."
    )
    group.add_argument(
        "--get-current-profile",
        action="store_true",
        help="Gets the current profile.",
    )
    group.add_argument(
        "--primary-resolution",
        action="store_true",
        help="Gets the primary output's resolution.",
    )
    args = parser.parse_args(args=argv)
    configure_logging(args)

    if args.state:
        print(XRandr().screen())
    elif args.primary_resolution:
        configuration = XRandr().screen().primary_output.configuration
        if configuration:
            print(configuration.mode.resolution)
    else:
        settings = Settings.from_json(
            gui_config_dir() / "output-profiles.json"
        )
        selector = ProfileSelector(settings)
        if args.list:
            for profile in settings.profiles:
                print(profile)
        elif args.get_current_profile:
            print(selector.current_profile)
        elif args.cycle_primary:
            selector.cycle_primary_output()
        elif args.cycle_profile:
            if args.default_profile:
                next_profile = selector.default_profile
            elif args.cycle_profile:
                next_profile = selector.next_valid_profile()
            elif args.profile:
                next_profile = args.profile

            if next_profile:
                selector.apply_profile(next_profile)
            else:
                return 1
        elif args.cycle_pactl_sink:
            selector.cycle_pactl_sink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
