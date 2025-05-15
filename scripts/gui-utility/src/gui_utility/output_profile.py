from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
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
    connected_monitors: set[str]
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_profiles": [
                {
                    "connected_monitors": list(p.connected_monitors),
                    "profile_name": p.profile_name,
                }
                for p in self.default_profiles
            ],
            "profiles": {
                name: {
                    "pactl_sink_regex": profile.pactl_sink_regex,
                    "monitors": {
                        mon_name: {
                            "layout": {
                                "resolution": {
                                    "width": m.layout.resolution.width,
                                    "height": m.layout.resolution.height,
                                },
                                "position": {
                                    "x": m.layout.position.x,
                                    "y": m.layout.position.y,
                                },
                            },
                            "primary": m.primary,
                            "refresh_rate": m.refresh_rate,
                        }
                        for mon_name, m in profile.monitors.items()
                    },
                }
                for name, profile in self.profiles.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        default_profiles = [
            DefaultProfile(
                connected_monitors=set(p["connected_monitors"]),
                profile_name=p["profile_name"],
            )
            for p in d.get("default_profiles", [])
        ]

        profiles = {
            name: Profile(
                pactl_sink_regex=p["pactl_sink_regex"],
                monitors={
                    mon_name: MonitorState(
                        layout=Layout(
                            resolution=Resolution(
                                width=m["layout"]["resolution"]["width"],
                                height=m["layout"]["resolution"]["height"],
                            ),
                            position=Coordinate(
                                x=m["layout"]["position"]["x"],
                                y=m["layout"]["position"]["y"],
                            ),
                        ),
                        primary=m["primary"],
                        refresh_rate=m["refresh_rate"],
                    )
                    for mon_name, m in p["monitors"].items()
                },
            )
            for name, p in d.get("profiles", {}).items()
        }

        return cls(default_profiles=default_profiles, profiles=profiles)

    def to_json(self, path: Path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_json(cls, path: Path) -> Settings:
        with open(path) as f:
            return cls.from_dict(json.load(f))


@dataclass
class ProfileSelector:
    settings: Settings
    pactl: PACtl = field(default_factory=lambda: PACtl())
    xrandr: XRandr = field(default_factory=lambda: XRandr())
    state: DisplayState = field(init=False)

    def __post_init__(self) -> None:
        self.update_state()

    def update_state(self) -> None:
        self.state = self.xrandr.state()

    def get_default_profile(self) -> str:
        logger.debug("determining default profile...")
        connected_monitors = set(self.state.connected_monitors)

        best_match = max(
            (
                default_profile.profile_name
                for default_profile in self.settings.default_profiles
                if default_profile.connected_monitors <= connected_monitors
            ),
            key=len,
            default=None,
        )
        if not best_match:
            logger.warning("could not find a matching default profile.")
            return ""
        logger.info(f"default profile determined: {best_match}")
        return best_match

    def get_current_profile(self) -> str:
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

    def next_profile(self, name: str = "") -> str:
        if not name:
            name = self.get_current_profile()
            if not name:
                return ""
        logger.debug("determining next profile...")
        profile_names = list(self.settings.profiles.keys())
        next_index = (profile_names.index(name) + 1) % len(profile_names)
        next_profile = profile_names[next_index]
        logger.info(f"next profile determined: {next_profile}")
        return next_profile

    def apply_profile(self, name: str) -> None:
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
    args = parser.parse_args(args=argv)
    configure_logging(args)

    settings = Settings.from_json(
        Path(os.environ["HOME"]) / ".output-profiles.json"
    )
    selector = ProfileSelector(settings)
    if args.list:
        for profile in settings.profiles:
            print(profile)
    else:
        if args.default:
            next_profile = selector.get_default_profile()
        elif args.cycle:
            next_profile = selector.next_profile()
        elif args.profile:
            next_profile = args.profile

        if next_profile:
            selector.apply_profile(next_profile)
        else:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
