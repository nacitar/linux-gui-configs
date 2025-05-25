from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property

from .cli_tool import CLITool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class Position:
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.x:+}{self.y:+}"

    def to_cli_str(self) -> str:
        return f"{self.x}x{self.y}"


@dataclass(frozen=True, order=True)
class Resolution:
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True, order=True)
class Mode:
    resolution: Resolution
    refresh_rate: Decimal | None

    def __str__(self) -> str:
        if self.refresh_rate:
            return f"{self.resolution}@{self.refresh_rate}hz"
        return str(self.resolution)


@dataclass(frozen=True)
class EDIDInfo:
    reported_modes: frozenset[Mode]
    preferred_mode: Mode | None

    @cached_property
    def sorted(self) -> tuple[Mode, ...]:
        preferred_resolution_modes: list[Mode] = []
        if self.preferred_mode:
            preferred_resolution_modes = [self.preferred_mode] + sorted(
                (
                    mode
                    for mode in self.reported_modes.difference(
                        {self.preferred_mode}
                    )
                    if mode.resolution == self.preferred_mode.resolution
                ),
                reverse=True,
            )

        return tuple(
            preferred_resolution_modes
            + sorted(
                self.reported_modes.difference(preferred_resolution_modes),
                reverse=True,
            )
        )

    @cached_property
    def default_mode(self) -> Mode:
        if self.preferred_mode:
            return self.preferred_mode
        return self.sorted[0]

    def supports_mode(self, mode: Mode) -> bool:
        return mode in self.reported_modes


@dataclass(frozen=True)
class Configuration:
    mode: Mode
    position: Position


@dataclass(frozen=True)
class Output:
    name: str
    connected: bool
    edid: EDIDInfo | None
    configuration: Configuration | None
    primary: bool

    def __str__(self) -> str:
        lines: list[str] = []
        output_parts: list[str] = [
            self.name,
            "connected" if self.connected else "disconnected",
        ]
        if self.primary:
            output_parts.append("primary")
        if self.configuration:
            output_parts.append(
                f"{self.configuration.mode.resolution}"
                f"{self.configuration.position}"
            )
        lines.append(" ".join(output_parts))
        if self.edid:  # outputs in the same order xrandr does
            rates: list[str] = []
            resolution: Resolution | None = None

            def add_resolution() -> None:
                if rates:
                    lines.append(f"   {resolution}\t{'\t'.join(rates)}")

            for mode in self.edid.sorted:
                if not resolution or resolution != mode.resolution:
                    add_resolution()
                    rates = []
                    resolution = mode.resolution
                is_current = (
                    self.configuration and self.configuration.mode == mode
                )
                is_preferred = self.edid.preferred_mode == mode
                suffix = ("*" if is_current else " ") + (
                    "+" if is_preferred else " "
                )
                rates.append(f"{mode.refresh_rate}{suffix}")
            add_resolution()
        return os.linesep.join(lines)


@dataclass(frozen=True)
class Screen:
    """
    Implements a __str__ that outputs SIMILARLY to xrandr, but not exactly,
    given that not all of the same data is included.  Useful for diagnosis.
    """

    name: str
    combined_resolution: Resolution
    outputs: tuple[Output, ...]

    @cached_property
    def connected_outputs(self) -> tuple[Output, ...]:
        return tuple(output for output in self.outputs if output.connected)

    @cached_property
    def active_outputs(self) -> tuple[Output, ...]:
        return tuple(output for output in self.outputs if output.configuration)

    @cached_property
    def connected_output_names(self) -> tuple[str, ...]:
        return tuple(output.name for output in self.connected_outputs)

    @cached_property
    def active_output_names(self) -> tuple[str, ...]:
        return tuple(output.name for output in self.active_outputs)

    @cached_property
    def primary_output(self) -> Output:
        primary_outputs = [
            output
            for output in self.outputs
            if output.configuration and output.primary
        ]
        if len(primary_outputs) > 1:
            raise AssertionError("Multiple primary outputs!")
        if primary_outputs:
            return primary_outputs[0]
        if self.active_outputs:
            return self.active_outputs[0]
        raise LookupError("no primary output because no active outputs")

    def __str__(self) -> str:
        return os.linesep.join(
            [f"Screen {self.name}: current {self.combined_resolution}"]
            + [str(output) for output in self.outputs]
        )


_SCREEN_PATTERN = re.compile(
    r"Screen (?P<name>[^:]+):.*"
    r" current (?P<width>\d+) x (?P<height>\d+)(,|\s|$)"
)
_OUTPUT_PATTERN = re.compile(
    r"(?P<name>[^\s]+) (?P<state>(dis)?connected)( (?P<primary>primary))?"
    r"( (?P<width>\d+)x(?P<height>\d+)\+(?P<x>\d+)\+(?P<y>\d+))?"
    r"(\s|$).*"
)
_REFRESH_RATE_PATTERN = re.compile(
    r"\s*(?P<rate>\d+(\.\d+)?)(?P<current>\*)?\s*(?P<preferred>\+)?"
)
_SUPPORTED_RESOLUTION_PATTERN = re.compile(
    r"\s+(?P<width>\d+)x(?P<height>\d+)(?P<remaining>.*)$"
)


@dataclass
class XRandr:
    """
    A wrapper for invoking xrandr, and parsing the state from 'xrandr --query'

    Does not ingest:
        - screen minimum/maximum resolutions
        - mode rotations/reflections
        - physical display sizes
    """

    tool: CLITool = field(default_factory=lambda: CLITool("xrandr"))

    def invoke(self, arguments: list[str]) -> str:
        return self.tool.invoke(arguments)

    def state(self) -> Screen:
        screen_name = ""
        combined_resolution: Resolution | None = None
        outputs: list[Output] = []

        output_name: str
        output_connected: bool
        output_primary: bool
        output_resolution: Resolution | None
        output_refresh_rate: Decimal | None = None
        output_position: Position | None
        output_edid_modes: list[Mode]
        output_edid_preferred_mode: Mode | None
        output_configuration: Configuration | None

        def clear_output() -> None:
            nonlocal output_name
            output_name = ""
            nonlocal output_connected
            output_connected = False
            nonlocal output_primary
            output_primary = False
            nonlocal output_resolution
            output_resolution = None
            nonlocal output_refresh_rate
            output_refresh_rate = None
            nonlocal output_position
            output_position = None
            nonlocal output_edid_modes
            output_edid_modes = []
            nonlocal output_edid_preferred_mode
            output_edid_preferred_mode = None
            nonlocal output_configuration
            output_configuration = None

        clear_output()

        def add_pending_output() -> None:
            if not output_name:
                return
            if output_edid_modes or output_edid_preferred_mode:
                edid = EDIDInfo(
                    reported_modes=frozenset(output_edid_modes),
                    preferred_mode=output_edid_preferred_mode,
                )
            else:
                edid = None
            if output_resolution and output_position:
                configuration = Configuration(
                    mode=Mode(
                        resolution=output_resolution,
                        refresh_rate=output_refresh_rate,
                    ),
                    position=output_position,
                )
            else:
                configuration = None
            outputs.append(
                Output(
                    name=output_name,
                    connected=output_connected,
                    edid=edid,
                    configuration=configuration,
                    primary=output_primary,
                )
            )
            clear_output()

        for line in self.tool.invoke(["--query"]).splitlines():
            if match := _SUPPORTED_RESOLUTION_PATTERN.match(line):
                if not output_name:
                    raise AssertionError(
                        "EDID line matched, but no current output"
                    )

                edid_resolution = Resolution(
                    width=int(match.group("width")),
                    height=int(match.group("height")),
                )
                for match in re.finditer(
                    _REFRESH_RATE_PATTERN, match.group("remaining")
                ):
                    edid_mode = Mode(
                        resolution=edid_resolution,
                        refresh_rate=Decimal(match.group("rate")),
                    )
                    output_edid_modes.append(edid_mode)
                    if match.group("current"):  # TODO: check double set?
                        output_refresh_rate = edid_mode.refresh_rate
                    if match.group("preferred"):  # TODO: check double set?
                        output_edid_preferred_mode = edid_mode
            else:
                add_pending_output()
                if match := _SCREEN_PATTERN.match(line):
                    if screen_name:
                        raise AssertionError("multiple screens!")
                    screen_name = match.group("name")
                    if (width := match.group("width")) and (
                        height := match.group("height")
                    ):
                        combined_resolution = Resolution(
                            width=int(width), height=int(height)
                        )
                elif match := _OUTPUT_PATTERN.match(line):
                    output_name = match.group("name")
                    output_connected = bool(
                        match.group("state") == "connected"
                    )
                    output_primary = bool(match.group("primary"))

                    if match.group("width"):  # the rest must be present too
                        output_resolution = Resolution(
                            width=int(match.group("width")),
                            height=int(match.group("height")),
                        )
                        output_position = Position(
                            x=int(match.group("x")), y=int(match.group("y"))
                        )
        add_pending_output()

        if not screen_name or not combined_resolution:
            raise AssertionError("display (virtual screen) missing fields.")
        return Screen(
            name=screen_name,
            combined_resolution=combined_resolution,
            outputs=tuple(outputs),
        )
