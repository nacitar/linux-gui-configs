from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from enum import IntEnum, auto, unique
from functools import cached_property
from typing import Any, Generic, Optional, Protocol, Sequence, TypeVar

from evdev import InputDevice, ecodes, list_devices

from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class InputRange:
    minimum: int
    maximum: int
    flat: int
    fuzz: int

    @cached_property
    def negative_flat(self) -> int:
        return -self.flat

    @cached_property
    def half_maximum(self) -> int:
        return self.maximum // 2

    @cached_property
    def half_minimum(self) -> int:
        return self.minimum // 2

    @cached_property
    def abs_minimum(self) -> int:
        return abs(self.minimum)

    def clamp(self, value: int) -> int:
        if value > self.negative_flat and value < self.flat:
            return 0
        if value < self.minimum:
            return self.minimum
        if value > self.maximum:
            return self.maximum
        return value

    def as_percentage(self, value: int) -> float:
        if value < 0:
            return value / self.abs_minimum
        return value / self.maximum

    def is_pressed(self, value: int) -> bool:
        return value > self.half_maximum or value < self.half_minimum


@dataclass
class Profile(Generic[T]):
    mapping: dict[str, T]


@dataclass(kw_only=True, frozen=True)
class Identifier(Generic[T]):
    user: T | None
    internal: str


@dataclass(kw_only=True, frozen=True)
class Input(Generic[T]):
    identifier: Identifier[T]
    range: InputRange


class GamepadMonitor(Protocol[T]):
    def sync_handler(self, controller: Controller[T]) -> bool: ...

    def update_handler(
        self, controller: Controller[T], input: Input[T], value: int
    ) -> bool: ...


@dataclass(kw_only=True)
class Controller(Generic[T]):
    device: InputDevice[Any]
    profile: Profile[T] | None

    forced_internal_names: dict[int, dict[int, str]] = field(
        default_factory=dict
    )

    inputs: dict[int, dict[int, Input[T]]] = field(
        default_factory=dict, init=False
    )
    _last_raw_value: dict[int, dict[int, int]] = field(
        default_factory=dict, init=False
    )

    def _get_internal_name(
        self, code: int, table: dict[int, str | tuple[str]]
    ) -> str:
        names = table.get(code)
        if names:
            if isinstance(names, str):
                names = (names,)
            if self.profile:
                for name in names:
                    if name in self.profile.mapping:
                        return name
            logger.warning(
                f"No profile-mapped names in list: {", ".join(names)}"
            )
            return names[0]
        return ""

    def __post_init__(self) -> None:
        capabilities = self.device.capabilities(absinfo=False)
        for ev_type in [ecodes.EV_KEY, ecodes.EV_ABS]:
            for code in capabilities.get(ev_type, []):
                name = self.forced_internal_names.get(ev_type, {}).get(
                    code, ""
                )
                if ev_type == ecodes.EV_ABS:
                    name = (
                        name
                        or self._get_internal_name(code, ecodes.ABS)
                        or f"ABS_{code}"
                    )
                    raw_absinfo = self.device.absinfo(code)
                    input_range = InputRange(
                        minimum=raw_absinfo.min,
                        maximum=raw_absinfo.max,
                        flat=raw_absinfo.flat,
                        fuzz=raw_absinfo.fuzz,
                    )
                else:
                    name = (
                        name
                        or self._get_internal_name(code, ecodes.BTN)
                        or self._get_internal_name(code, ecodes.KEY)
                        or f"KEY_{code}"
                    )
                    input_range = InputRange(
                        minimum=0, maximum=1, flat=0, fuzz=0
                    )
                if not name:
                    raise AssertionError(
                        f"All inputs should be named by now but {code} isn't."
                    )
                if self.profile:
                    user_identifier = self.profile.mapping.get(name)
                else:
                    user_identifier = None

                self.inputs.setdefault(ev_type, {})[code] = Input(
                    identifier=Identifier[T](
                        user=user_identifier, internal=name
                    ),
                    range=input_range,
                )

    def read_loop(self, monitor: GamepadMonitor[T]) -> None:
        self._last_raw_value.clear()
        updated = False
        for event in self.device.read_loop():
            if event.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                input = self.inputs.get(event.type, {}).get(event.code, None)
                if input:
                    event_type_section = self._last_raw_value.setdefault(
                        event.type, {}
                    )
                    last_value = event_type_section.get(event.code)
                    if last_value is not None:
                        if abs(event.value - last_value) < input.range.fuzz:
                            logger.debug(
                                "Skipping update within fuzz range"
                                f": {event.type}, {event.code}"
                            )
                            continue
                    event_type_section[event.code] = event.value
                    updated = True
                    if not monitor.update_handler(self, input, event.value):
                        break
                else:
                    logger.warning(
                        f"Skipping unknown input: {event.type}, {event.code}"
                    )
            elif event.type == ecodes.EV_SYN:
                if updated:
                    updated = False
                    if not monitor.sync_handler(self):
                        break


def find_controller(
    name_filter: str | None = None,
) -> Optional[InputDevice[Any]]:
    for path in list_devices():
        dev = InputDevice(path)
        if name_filter is None or name_filter.lower() in dev.name.lower():
            return dev
    return None


@unique
class GamepadInput(IntEnum):
    A = auto()
    B = auto()
    X = auto()
    Y = auto()
    SELECT = auto()
    START = auto()
    GUIDE = auto()
    LB = auto()
    RB = auto()
    LS = auto()
    RS = auto()
    LX = auto()
    LY = auto()
    RX = auto()
    RY = auto()
    LT = auto()
    RT = auto()
    DX = auto()
    DY = auto()


XINPUT_PROFILE = Profile[GamepadInput](
    mapping={
        "BTN_SOUTH": GamepadInput.A,
        "BTN_EAST": GamepadInput.B,
        "BTN_NORTH": GamepadInput.Y,
        "BTN_WEST": GamepadInput.X,
        "BTN_SELECT": GamepadInput.SELECT,
        "BTN_START": GamepadInput.START,
        "BTN_MODE": GamepadInput.GUIDE,
        "BTN_TL": GamepadInput.LB,
        "BTN_TR": GamepadInput.RB,
        "BTN_THUMBL": GamepadInput.LS,
        "BTN_THUMBR": GamepadInput.RS,
        "ABS_X": GamepadInput.LX,
        "ABS_Y": GamepadInput.LY,
        "ABS_Z": GamepadInput.LT,
        "ABS_RZ": GamepadInput.RT,
        "ABS_RX": GamepadInput.RX,
        "ABS_RY": GamepadInput.RY,
        "ABS_HAT0X": GamepadInput.DX,
        "ABS_HAT0Y": GamepadInput.DY,
    }
)


@dataclass
class GamepadInputState:
    pressed: bool
    value: float

    def __str__(self) -> str:
        if self.pressed:
            return f"_{self.value}_"
        return str(self.value)


@dataclass
class TerminalGamepadMonitor:
    state: dict[GamepadInput, GamepadInputState] = field(default_factory=dict)

    def sync_handler(self, controller: Controller[GamepadInput]) -> bool:
        print(f"SYN: {self}")
        return True

    def update_handler(
        self,
        controller: Controller[GamepadInput],
        input: Input[GamepadInput],
        value: int,
    ) -> bool:
        if input.identifier.user:
            value = input.range.clamp(value)
            # not using setdefault to avoid constructing GamepadInputState
            input_state = self.state.get(input.identifier.user)
            if not input_state:
                input_state = GamepadInputState(pressed=False, value=0)
                self.state[input.identifier.user] = input_state
            input_state.pressed = input.range.is_pressed(value)
            input_state.value = input.range.as_percentage(value)
            logger.debug(f"Update: {input.identifier.user} = {value}")
        else:
            logger.warning(f"UNHANDLED: {input.identifier.internal} = {value}")
        return True

    def __repr__(self) -> str:
        return " ".join(
            [f"{input.name}={value}" for input, value in self.state.items()]
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Monitors the state of a gamepad."
    )
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)

    dev = find_controller("8bitdo")  # Or None to take the first one
    if dev is None:
        raise RuntimeError("Controller not found")

    print(f"Using device: {dev.path} ({dev.name})")
    controller = Controller(device=dev, profile=XINPUT_PROFILE)

    monitor = TerminalGamepadMonitor()
    try:
        controller.read_loop(monitor)
    except KeyboardInterrupt:
        print("\nExiting.")
    return 0
