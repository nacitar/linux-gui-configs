from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import sleep
from typing import Any, Sequence

from .cli_tool import CLITool
from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)


@dataclass
class Resolution:
    width: int
    height: int

    def __bool__(self) -> bool:
        return self.width != 0 and self.height != 0

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class Coordinate:
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.x}x{self.y}"


@dataclass
class Layout:
    resolution: Resolution = field(
        default_factory=lambda: Resolution(width=0, height=0)
    )
    position: Coordinate = field(default_factory=lambda: Coordinate(x=0, y=0))

    def __bool__(self) -> bool:
        return bool(self.resolution)


@dataclass
class DisplayState:
    name: str
    resolution: Resolution
    connected_monitors: list[str]
    active_monitors: list[str]
    primary_monitor: str


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


_SCREEN_PATTERN = re.compile(
    r"Screen (?P<name>[^:]+):.*"
    r" current (?P<width>\d+) x (?P<height>\d+)(,|\s|$)"
)
_MONITOR_PATTERN = re.compile(
    r"(?P<name>[^\s]+) (?P<state>(dis)?connected)( (?P<primary>primary))?"
    r"( (?P<width>\d+)x(?P<height>\d+)\+(?P<x>\d+)\+(?P<y>\d+))?"
    r"(\s|$).*"
)


@dataclass
class XRandr:
    tool: CLITool = field(default_factory=lambda: CLITool("xrandr"))

    def invoke(self, arguments: list[str]) -> str:
        return self.tool.invoke(arguments)

    def state(self) -> DisplayState:
        display_name = ""
        resolution: Resolution | None = None
        connected_monitors: list[str] = []
        active_monitors: list[str] = []
        primary_monitor: str = ""
        for line in self.tool.invoke(["--query"]).splitlines():
            if line.startswith(" "):
                continue
            if match := _SCREEN_PATTERN.match(line):
                if display_name:
                    raise AssertionError("multiple screens!")
                display_name = match.group("name")
                if (width := match.group("width")) and (
                    height := match.group("height")
                ):
                    resolution = Resolution(
                        width=int(width), height=int(height)
                    )
            elif match := _MONITOR_PATTERN.match(line):
                if match.group("state") == "connected":
                    name = match.group("name")
                    connected_monitors.append(name)
                    if match.group("primary"):
                        if primary_monitor:
                            raise AssertionError("multiple primary monitors.")
                        primary_monitor = name
                    if match.group("width"):
                        active_monitors.append(name)
        if not display_name or not resolution:
            raise AssertionError("display (virtual screen) missing fields.")
        if not primary_monitor and active_monitors:
            primary_monitor = active_monitors[0]
        return DisplayState(
            name=display_name,
            resolution=resolution,
            connected_monitors=connected_monitors,
            active_monitors=active_monitors,
            primary_monitor=primary_monitor,
        )


@dataclass
class DefaultProfile:
    connected_monitors: list[str]
    profile_name: str


@dataclass
class MonitorState:
    layout: Layout
    primary: bool
    refresh_rate: str


@dataclass
class Profile:
    pactl_sink_regex: str
    monitors: dict[str, MonitorState]


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
                    monitors={
                        output_name: MonitorState(
                            layout=Layout(
                                resolution=Resolution(
                                    **monitor_state["layout"]["resolution"]
                                ),
                                position=Coordinate(
                                    **monitor_state["layout"]["position"]
                                ),
                            ),
                            primary=monitor_state["primary"],
                            refresh_rate=monitor_state["refresh_rate"],
                        )
                        for output_name, monitor_state in profile[
                            "monitors"
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

    def root_pixmap_id(self) -> int:
        output = self.tool.invoke(["-root", "-notype", "_XROOTPMAP_ID"])
        index = output.find("0x")
        if index == -1:
            logger.error("Couldn't retrieve root pixmap id!")
            return -1
        return int(output[index + 2 :], 16)


@dataclass
class ProfileSelector:
    settings: Settings
    pactl: PACtl = field(default_factory=lambda: PACtl())
    xrandr: XRandr = field(default_factory=lambda: XRandr())
    xprop: XProp = field(default_factory=lambda: XProp())
    state: DisplayState = field(init=False)
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
        connected_monitors = set(self.state.connected_monitors)

        best_match = max(
            (
                default_profile.profile_name
                for default_profile in self.settings.default_profiles
                if set(default_profile.connected_monitors)
                <= connected_monitors
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
        active_monitors = set(self.state.active_monitors)
        match = max(
            (
                name
                for name, profile in self.settings.profiles.items()
                if set(profile.monitors.keys()) == active_monitors
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
        connected_monitors = set(self.state.connected_monitors)
        for next_profile in profile_list:
            profile = self.settings.profiles[next_profile]
            if set(profile.monitors.keys()) <= connected_monitors:
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

        any_monitor = False
        for monitor in self.state.connected_monitors:
            if monitor in profile.monitors:
                monitor_state = profile.monitors[monitor]
                xrandr_cli.extend(
                    [
                        "--output",
                        monitor,
                        "--mode",
                        str(monitor_state.layout.resolution),
                    ]
                )
                if monitor_state.refresh_rate:
                    xrandr_cli.extend(["--rate", monitor_state.refresh_rate])
                xrandr_cli.extend(
                    ["--pos", str(monitor_state.layout.position)]
                )
                if monitor_state.primary:
                    xrandr_cli.append("--primary")
                any_monitor = True
            else:
                xrandr_cli.extend(["--output", monitor, "--off"])
        if not any_monitor:
            raise AssertionError("no monitor would have been enabled!")

        old_root_pixmap_id = self.xprop.root_pixmap_id()
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

        on_output_change = gui_config_dir() / "on-output-change"
        if on_output_change.exists():
            delay_ms = 100
            remaining_ms = 5000
            while remaining_ms > 0:
                if self.xprop.root_pixmap_id() != old_root_pixmap_id:
                    logger.info("new root pixmap detected")
                    break
                logger.debug(f"waiting {delay_ms}ms for new root pixmap...")
                sleep(delay_ms / 1000)
                remaining_ms -= delay_ms
            if remaining_ms <= 0:
                logger.error("no new root pixmap within timeout.")

            print(CLITool(binary=str(on_output_change)).invoke([name]))


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
        help="Apply the default profile given currently connected monitors.",
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
    args = parser.parse_args(args=argv)
    configure_logging(args)

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
