from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from enum import IntEnum, auto, unique
from functools import cached_property
from typing import Protocol, Sequence

import sdl2  # type: ignore

from .log_utility import add_log_arguments, configure_logging
from .websocket_server import WebSocketBroadcaster

logger = logging.getLogger(__name__)


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


@dataclass(kw_only=True, frozen=True)
class Identifier:
    user: GamepadInput | None
    internal: str


@dataclass(kw_only=True, frozen=True)
class Input:
    identifier: Identifier
    range: InputRange


class GamepadMonitor(Protocol):
    def on_start(self, controller: SDLGameController, inputs: list[Input]) -> None:
        ...

    def on_sync(self, controller: SDLGameController) -> None:
        ...

    def on_update(self, controller: SDLGameController, input: Input, value: int) -> None:
        ...


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


BUTTON_RANGE = InputRange(minimum=0, maximum=1, flat=0, fuzz=0)
STICK_RANGE = InputRange(minimum=-32768, maximum=32767, flat=0, fuzz=0)
TRIGGER_RANGE = InputRange(minimum=0, maximum=32767, flat=0, fuzz=0)
DPAD_RANGE = InputRange(minimum=-1, maximum=1, flat=0, fuzz=0)


SDL_BUTTON_MAP: dict[int, tuple[str, GamepadInput]] = {
    sdl2.SDL_CONTROLLER_BUTTON_A: ("SDL_CONTROLLER_BUTTON_A", GamepadInput.A),
    sdl2.SDL_CONTROLLER_BUTTON_B: ("SDL_CONTROLLER_BUTTON_B", GamepadInput.B),
    sdl2.SDL_CONTROLLER_BUTTON_X: ("SDL_CONTROLLER_BUTTON_X", GamepadInput.X),
    sdl2.SDL_CONTROLLER_BUTTON_Y: ("SDL_CONTROLLER_BUTTON_Y", GamepadInput.Y),
    sdl2.SDL_CONTROLLER_BUTTON_BACK: (
        "SDL_CONTROLLER_BUTTON_BACK",
        GamepadInput.SELECT,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_START: (
        "SDL_CONTROLLER_BUTTON_START",
        GamepadInput.START,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_GUIDE: (
        "SDL_CONTROLLER_BUTTON_GUIDE",
        GamepadInput.GUIDE,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_LEFTSHOULDER: (
        "SDL_CONTROLLER_BUTTON_LEFTSHOULDER",
        GamepadInput.LB,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: (
        "SDL_CONTROLLER_BUTTON_RIGHTSHOULDER",
        GamepadInput.RB,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_LEFTSTICK: (
        "SDL_CONTROLLER_BUTTON_LEFTSTICK",
        GamepadInput.LS,
    ),
    sdl2.SDL_CONTROLLER_BUTTON_RIGHTSTICK: (
        "SDL_CONTROLLER_BUTTON_RIGHTSTICK",
        GamepadInput.RS,
    ),
}

SDL_AXIS_MAP: dict[int, tuple[str, GamepadInput, InputRange]] = {
    sdl2.SDL_CONTROLLER_AXIS_LEFTX: (
        "SDL_CONTROLLER_AXIS_LEFTX",
        GamepadInput.LX,
        STICK_RANGE,
    ),
    sdl2.SDL_CONTROLLER_AXIS_LEFTY: (
        "SDL_CONTROLLER_AXIS_LEFTY",
        GamepadInput.LY,
        STICK_RANGE,
    ),
    sdl2.SDL_CONTROLLER_AXIS_RIGHTX: (
        "SDL_CONTROLLER_AXIS_RIGHTX",
        GamepadInput.RX,
        STICK_RANGE,
    ),
    sdl2.SDL_CONTROLLER_AXIS_RIGHTY: (
        "SDL_CONTROLLER_AXIS_RIGHTY",
        GamepadInput.RY,
        STICK_RANGE,
    ),
    sdl2.SDL_CONTROLLER_AXIS_TRIGGERLEFT: (
        "SDL_CONTROLLER_AXIS_TRIGGERLEFT",
        GamepadInput.LT,
        TRIGGER_RANGE,
    ),
    sdl2.SDL_CONTROLLER_AXIS_TRIGGERRIGHT: (
        "SDL_CONTROLLER_AXIS_TRIGGERRIGHT",
        GamepadInput.RT,
        TRIGGER_RANGE,
    ),
}


def _build_inputs() -> dict[GamepadInput, Input]:
    inputs: dict[GamepadInput, Input] = {}
    for internal, mapped in SDL_BUTTON_MAP.values():
        inputs[mapped] = Input(
            identifier=Identifier(user=mapped, internal=internal),
            range=BUTTON_RANGE,
        )

    for internal, mapped, input_range in SDL_AXIS_MAP.values():
        inputs[mapped] = Input(
            identifier=Identifier(user=mapped, internal=internal),
            range=input_range,
        )

    inputs[GamepadInput.DX] = Input(
        identifier=Identifier(user=GamepadInput.DX, internal="SDL_DPAD_X"),
        range=DPAD_RANGE,
    )
    inputs[GamepadInput.DY] = Input(
        identifier=Identifier(user=GamepadInput.DY, internal="SDL_DPAD_Y"),
        range=DPAD_RANGE,
    )
    return inputs


@dataclass
class SDLGameController:
    name_filter: str | None = "8bitdo"
    _controller: sdl2.SDL_GameController = field(init=False)
    _instance_id: int = field(init=False)
    inputs: dict[GamepadInput, Input] = field(default_factory=_build_inputs, init=False)
    _dpad_left: bool = field(default=False, init=False)
    _dpad_right: bool = field(default=False, init=False)
    _dpad_up: bool = field(default=False, init=False)
    _dpad_down: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            raise RuntimeError(
                f"SDL_Init failed: {sdl2.SDL_GetError().decode('utf-8')}"
            )

        sdl2.SDL_GameControllerEventState(sdl2.SDL_ENABLE)
        self._controller = self._open_controller()
        joystick = sdl2.SDL_GameControllerGetJoystick(self._controller)
        self._instance_id = int(sdl2.SDL_JoystickInstanceID(joystick))

    def _open_controller(self) -> sdl2.SDL_GameController:
        count = sdl2.SDL_NumJoysticks()
        for joystick_index in range(count):
            if sdl2.SDL_IsGameController(joystick_index) == 0:
                continue
            controller = sdl2.SDL_GameControllerOpen(joystick_index)
            if not controller:
                logger.warning(
                    "Failed to open SDL game controller index %s: %s",
                    joystick_index,
                    sdl2.SDL_GetError().decode("utf-8"),
                )
                continue

            name_ptr = sdl2.SDL_GameControllerName(controller)
            if name_ptr is None:
                name = "unknown"
            else:
                name = name_ptr.decode("utf-8")

            if self.name_filter and self.name_filter.lower() not in name.lower():
                sdl2.SDL_GameControllerClose(controller)
                continue

            print(f"Using controller: {name}")
            return controller

        raise RuntimeError("Controller not found")

    def close(self) -> None:
        if getattr(self, "_controller", None):
            sdl2.SDL_GameControllerClose(self._controller)
        sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_GAMECONTROLLER)

    def read_loop(self, monitor: GamepadMonitor) -> None:
        monitor.on_start(self, list(self.inputs.values()))
        event = sdl2.SDL_Event()
        while True:
            if sdl2.SDL_WaitEventTimeout(event, 100) == 0:
                error = sdl2.SDL_GetError().decode("utf-8")
                if error:
                    raise RuntimeError(f"SDL_WaitEventTimeout failed: {error}")
                continue

            updated = False
            if event.type == sdl2.SDL_CONTROLLERAXISMOTION:
                if int(event.caxis.which) != self._instance_id:
                    continue
                mapped = SDL_AXIS_MAP.get(int(event.caxis.axis))
                if mapped is None:
                    continue
                _, target, input_range = mapped
                input = self.inputs[target]
                value = input_range.clamp(int(event.caxis.value))
                monitor.on_update(self, input, value)
                updated = True

            elif event.type in (
                sdl2.SDL_CONTROLLERBUTTONDOWN,
                sdl2.SDL_CONTROLLERBUTTONUP,
            ):
                if int(event.cbutton.which) != self._instance_id:
                    continue
                button = int(event.cbutton.button)
                value = 1 if event.type == sdl2.SDL_CONTROLLERBUTTONDOWN else 0

                if button == sdl2.SDL_CONTROLLER_BUTTON_DPAD_LEFT:
                    self._dpad_left = value == 1
                    updated = self._emit_dpad_x(monitor)
                elif button == sdl2.SDL_CONTROLLER_BUTTON_DPAD_RIGHT:
                    self._dpad_right = value == 1
                    updated = self._emit_dpad_x(monitor)
                elif button == sdl2.SDL_CONTROLLER_BUTTON_DPAD_UP:
                    self._dpad_up = value == 1
                    updated = self._emit_dpad_y(monitor)
                elif button == sdl2.SDL_CONTROLLER_BUTTON_DPAD_DOWN:
                    self._dpad_down = value == 1
                    updated = self._emit_dpad_y(monitor)
                else:
                    button_mapping = SDL_BUTTON_MAP.get(button)
                    if button_mapping is None:
                        continue
                    _, target = button_mapping
                    input = self.inputs[target]
                    monitor.on_update(self, input, value)
                    updated = True

            elif event.type == sdl2.SDL_CONTROLLERDEVICEREMOVED:
                if int(event.cdevice.which) == self._instance_id:
                    raise RuntimeError("Controller disconnected")

            if updated:
                monitor.on_sync(self)

    def _emit_dpad_x(self, monitor: GamepadMonitor) -> bool:
        value = 0
        if self._dpad_left:
            value = -1
        elif self._dpad_right:
            value = 1
        monitor.on_update(self, self.inputs[GamepadInput.DX], value)
        return True

    def _emit_dpad_y(self, monitor: GamepadMonitor) -> bool:
        value = 0
        if self._dpad_up:
            value = -1
        elif self._dpad_down:
            value = 1
        monitor.on_update(self, self.inputs[GamepadInput.DY], value)
        return True


@dataclass
class TerminalGamepadMonitor:
    websocket_broadcaster: WebSocketBroadcaster | None = None
    state: dict[str, float] = field(default_factory=dict)

    def on_start(self, controller: SDLGameController, inputs: list[Input]) -> None:
        logger.info(f"START: initializing {len(inputs)} inputs.")
        self.state = {
            input.identifier.user.name: 0.0
            for input in inputs
            if input.identifier.user
        }
        self.broadcast()

    def on_sync(self, controller: SDLGameController) -> None:
        self.broadcast()

    def broadcast(self) -> None:
        print(f"STATE: {self}")
        if self.websocket_broadcaster:
            self.websocket_broadcaster.send_state(self.state)

    def on_update(
        self,
        controller: SDLGameController,
        input: Input,
        value: int,
    ) -> None:
        if input.identifier.user:
            name = input.identifier.user.name
            logger.debug(f"Update: {name} = {value}")
            self.state[name] = input.range.as_percentage(value)
        else:
            logger.warning(f"UNHANDLED: {input.identifier.internal} = {value}")

    def __repr__(self) -> str:
        return " ".join(f"{name}={value}" for name, value in self.state.items())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Monitors the state of a gamepad."
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Allow websocket connections from other machines on the LAN.",
    )
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)

    websocket_broadcaster = WebSocketBroadcaster(
        8765,
        host="0.0.0.0" if args.lan else "localhost",
        path="/gamepad-overlay",
        banner="gamepad-overlay",
    )

    monitor = TerminalGamepadMonitor(
        websocket_broadcaster=websocket_broadcaster
    )
    controller = SDLGameController(name_filter="8bitdo")
    try:
        controller.read_loop(monitor)
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        controller.close()
    return 0
