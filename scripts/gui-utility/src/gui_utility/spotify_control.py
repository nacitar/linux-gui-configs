from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from .cli_tool import CLITool
from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)


@dataclass
class DBusValue:
    type: str
    value: Any

    @classmethod
    def from_json(cls, value: str) -> DBusValue:
        data = json.loads(value)
        return cls(type=data["type"], value=data["data"])

    @classmethod
    def from_int(
        cls, value: int, *, unsigned: bool = False, bits_64: bool = False
    ) -> DBusValue:
        types_by_bits = [["i", "u"], ["x", "t"]]
        return cls(
            type=types_by_bits[int(bits_64)][int(unsigned)], value=value
        )

    def __int__(self) -> int:
        if self.type in ["i", "u", "x", "t"]:
            return int(self.value)
        raise ValueError(f"not an integral type: {self.type}")

    @classmethod
    def from_bool(cls, value: bool) -> DBusValue:
        return cls(type="b", value=value)

    def __bool__(self) -> bool:
        if self.type == "b":
            return bool(self.value)
        raise ValueError(f"not a boolean type: {self.type}")

    @classmethod
    def from_float(cls, value: float) -> DBusValue:
        return cls(type="d", value=value)

    def __float__(self) -> float:
        if self.type == "d":
            return float(self.value)
        raise ValueError(f"not a floating point type: {self.type}")

    @classmethod
    def from_str(cls, value: str) -> DBusValue:
        return cls(type="s", value=value)

    def __str__(self) -> str:
        if self.type == "s":
            return str(self.value)
        raise ValueError(f"not a string type: {self.type}")

    def to_cli(self) -> list[str]:
        return [self.type, str(self.value)]


@dataclass
class BusCtl:
    tool: CLITool = field(default_factory=lambda: CLITool("busctl"))

    def invoke(self, arguments: list[str]) -> list[DBusValue]:
        return [
            DBusValue.from_json(line)
            for line in self.tool.invoke(
                ["--json=short", "--user"] + arguments
            ).splitlines()
        ]

    def get_properties(
        self,
        service: str,
        object_path: str,
        interface: str,
        properties: list[str],
    ) -> list[DBusValue]:
        result = self.invoke(
            ["get-property", service, object_path, interface] + properties
        )
        if len(result) != len(properties):
            raise ValueError(
                "Property count doesn't match: "
                f"requested {len(properties)} but received {len(result)}"
            )
        return result

    def set_property(
        self,
        service: str,
        object_path: str,
        interface: str,
        property: str,
        value: DBusValue,
    ) -> None:
        self.invoke(
            ["set-property", service, object_path, interface, property]
            + value.to_cli()
        )

    def call(
        self,
        service: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: list[DBusValue] | None = None,
    ) -> list[DBusValue]:
        if arguments is None:
            arguments = []
        return self.invoke(
            ["call", service, object_path, interface, method]
            + [arg for argument in arguments for arg in argument.to_cli()]
        )


# TODO: parsing of Metadata property.
@dataclass
class Spotify:
    service: str = "org.mpris.MediaPlayer2.spotify"
    object_path: str = "/org/mpris/MediaPlayer2"
    interface: str = "org.mpris.MediaPlayer2.Player"
    busctl: BusCtl = field(default_factory=lambda: BusCtl())

    def get_properties(self, properties: list[str]) -> list[DBusValue]:
        return self.busctl.get_properties(
            service=self.service,
            object_path=self.object_path,
            interface=self.interface,
            properties=properties,
        )

    def set_property(self, property: str, value: DBusValue) -> None:
        self.busctl.set_property(
            service=self.service,
            object_path=self.object_path,
            interface=self.interface,
            property=property,
            value=value,
        )

    def call(
        self, method: str, arguments: list[DBusValue] | None = None
    ) -> list[DBusValue]:
        return self.busctl.call(
            service=self.service,
            object_path=self.object_path,
            interface=self.interface,
            method=method,
            arguments=arguments,
        )

    @property
    def volume(self) -> int:
        return int(round(float(self.get_properties(["Volume"])[0]) * 100.0, 0))

    # TODO: smart shuffle seems impossible to set; this turns it off when used
    # and doesn't indicate if it's on either.
    @property
    def shuffle(self) -> bool:
        return bool(self.get_properties(["Shuffle"])[0])

    def shuffle_set(self, enabled: bool) -> None:
        self.set_property("Shuffle", DBusValue.from_bool(enabled))

    def shuffle_toggle(self) -> bool:
        enabled = not self.shuffle
        self.shuffle_set(enabled)
        return enabled

    @property
    def repeat(self) -> str:
        """returns one of: 'None', 'Playlist', 'Track'"""
        return str(self.get_properties(["LoopStatus"])[0])

    def repeat_set(self, value: str) -> None:
        if value not in ["None", "Playlist", "Track"]:
            raise ValueError(f"Invalid repeat option: {value}")
        self.set_property("LoopStatus", DBusValue.from_str(value))

    def volume_set(self, percent: int) -> None:
        self.set_property(
            "Volume", DBusValue.from_float(max(0.0, min(percent / 100.0, 1.0)))
        )

    def volume_adjust(self, percent: int) -> float:
        volume = self.volume + percent
        self.volume_set(volume)
        return volume

    def play_toggle(self) -> None:
        self.call("PlayPause")

    def play(self) -> None:
        self.call("Play")

    def pause(self) -> None:
        self.call("Pause")

    def stop(self) -> None:
        self.call("Stop")

    def next(self) -> None:
        self.call("Next")

    def previous(self) -> None:
        self.call("Previous")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Uses dbus to control spotify."
    )
    add_log_arguments(parser)
    volume_group = parser.add_mutually_exclusive_group()
    volume_group.add_argument(
        "--volume-set",
        type=int,
        help="Sets the volume to the specified integer percent.",
    )
    volume_group.add_argument(
        "--volume-adjust",
        type=int,
        help="Adjusts the volume up/down by the specified integer percent.",
    )
    operation_group = parser.add_mutually_exclusive_group()
    operation_group.add_argument(
        "--play", action="store_true", help="Plays current stream."
    )
    operation_group.add_argument(
        "--pause", action="store_true", help="Pauses current stream."
    )
    operation_group.add_argument(
        "--play-toggle",
        action="store_true",
        help="Toggles current stream's play/pause status.",
    )
    operation_group.add_argument(
        "--stop", action="store_true", help="Stops current stream."
    )
    operation_group.add_argument(
        "--next", action="store_true", help="Advances to the next stream."
    )
    operation_group.add_argument(
        "--previous",
        action="store_true",
        help="Return to start of current stream or to previous stream.",
    )
    args = parser.parse_args(args=argv)
    configure_logging(args)
    spotify = Spotify()
    if args.volume_set is not None:
        spotify.volume_set(args.volume_set)
    if args.volume_adjust is not None:
        spotify.volume_adjust(args.volume_adjust)
    if args.play:
        spotify.play()
    if args.pause:
        spotify.pause()
    if args.play_toggle:
        spotify.play_toggle()
    if args.stop:
        spotify.stop()
    if args.next:
        spotify.next()
    if args.previous:
        spotify.previous()
    return 0
