from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from evdev import InputDevice, ecodes, list_devices

from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputRange:
    minimum: int
    maximum: int
    _minimum_pressed: int = field(init=False)
    _delta: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(  # because frozen
            self, "_minimum_pressed", (self.minimum + self.maximum) // 2
        )
        object.__setattr__(  # because frozen
            self, "_delta", (self.maximum - self.minimum)
        )

    def as_percentage(self, value: int) -> float:
        return (value - self.minimum) / self._delta

    def is_pressed(self, value: int) -> bool:
        return value > self._minimum_pressed


@dataclass(kw_only=True)
class InputState:
    range: InputRange
    value: int

    def is_pressed(self) -> bool:
        return self.range.is_pressed(self.value)

    def __float__(self) -> float:
        return self.range.as_percentage(self.value)


@dataclass(kw_only=True)
class Input:
    name: str
    state: InputState


class Controller:
    PREFERRED_NAMES: set[str] = {
        "BTN_SOUTH",
        "BTN_EAST",
        "BTN_NORTH",
        "BTN_WEST",
        "BTN_SELECT",
        "BTN_START",
        "BTN_MODE",
        "BTN_TL",
        "BTN_TR",
        "BTN_THUMBL",
        "BTN_THUMBR",
        "ABS_X",
        "ABS_Y",
        "ABS_Z",
        "ABS_RX",
        "ABS_RY",
        "ABS_RZ",
        "ABS_HAT0X",
        "ABS_HAT0Y",
    }

    # TODO: do something with this?
    XBOX_MAPPING: dict[str, str] = {
        "BTN_SOUTH": "A",
        "BTN_EAST": "B",
        "BTN_NORTH": "Y",
        "BTN_WEST": "X",
        "BTN_SELECT": "VIEW",  # or select?
        "BTN_START": "MENU",  # or start?
        "BTN_MODE": "XBOX",  # or guide?
        "BTN_TL": "LB",
        "BTN_TR": "RB",
        "BTN_THUMBL": "LS",
        "BTN_THUMBR": "RS",
        "ABS_X": "LX",
        "ABS_Y:": "LY",
        "ABS_Z": "LT",
        "ABS_RZ": "RT",
        "ABS_RX": "RX",
        "ABS_RY": "RY",
        "ABS_HAT0X": "PadX",
        "ABS_HAT0Y": "PadY",
    }

    @classmethod
    def get_input_name(
        cls, code: int, table: dict[int, str | tuple[str]]
    ) -> str:
        names = table.get(code)
        if names:
            if isinstance(names, str):
                names = (names,)
            for name in names:
                if name in cls.PREFERRED_NAMES:
                    return name
            logger.warning(f"No preferred name in list: {", ".join(names)}")
            return names[0]
        return ""

    def __init__(self, device: InputDevice[Any]) -> None:
        self.device = device
        self.inputs: dict[int, dict[int, Input]] = {}

        capabilities = device.capabilities(absinfo=False)

        for ev_type in [ecodes.EV_KEY, ecodes.EV_ABS]:
            for code in capabilities.get(ev_type, []):
                if ev_type == ecodes.EV_ABS:
                    name = Controller.get_input_name(code, ecodes.ABS)
                    raw_absinfo = device.absinfo(code)
                    input_range = InputRange(raw_absinfo.min, raw_absinfo.max)
                    value = raw_absinfo.value
                else:
                    name = Controller.get_input_name(
                        code, ecodes.BTN
                    ) or Controller.get_input_name(code, ecodes.KEY)
                    input_range = InputRange(0, 1)
                    value = int(code in device.active_keys(verbose=False))
                if not name:
                    raise AssertionError(
                        f"Could not find name for input code: {code}"
                    )
                self.inputs.setdefault(ev_type, {})[code] = Input(
                    name=name, state=InputState(range=input_range, value=value)
                )

    def update(self, code: int, value: int, input_type: int) -> None:
        input = self.inputs.get(input_type, {}).get(code, None)
        if input:
            input.state.value = value
        else:
            print(f"Skipping unknown input: {code}")

    def __repr__(self) -> str:
        return " ".join(
            [
                f"{input.name}={input.state.value}"
                for input_type, code_to_input in self.inputs.items()
                for input in code_to_input.values()
            ]
        )


def find_controller(
    name_filter: str | None = None,
) -> Optional[InputDevice[Any]]:
    for path in list_devices():
        dev = InputDevice(path)
        if name_filter is None or name_filter.lower() in dev.name.lower():
            return dev
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitors the battery status")
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)

    dev = find_controller("8bitdo")  # Or None to take the first one
    if dev is None:
        raise RuntimeError("Controller not found")

    print(f"Using device: {dev.path} ({dev.name})")
    controller = Controller(dev)
    try:
        while True:
            print(controller)  # duplicated in syn case
            for event in dev.read_loop():
                if event.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                    controller.update(event.code, event.value, event.type)
                elif event.type == ecodes.EV_SYN:
                    break
                    # so it can print the state again
    except KeyboardInterrupt:
        print("\nExiting.")
    return 0
