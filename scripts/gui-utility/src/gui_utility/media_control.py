from __future__ import annotations

import argparse
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .dbus import BusCtl, DBusValue
from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)
_MPRIS_ROOT_INTERFACE = "org.mpris.MediaPlayer2"
_MPRIS_SERVICE_PREFIX = f"{_MPRIS_ROOT_INTERFACE}."
_REGEX_PREFIX = "re:"


# TODO: parsing of Metadata property.
@dataclass(kw_only=True)
class MediaControl:
    services: list[str]
    object_path: str = "/org/mpris/MediaPlayer2"
    interface: str = "org.mpris.MediaPlayer2.Player"
    busctl: BusCtl = field(default_factory=lambda: BusCtl())

    def __post_init__(self) -> None:
        for i in range(len(self.services)):
            service = self.services[i]
            if not service:
                raise ValueError("service must be specified")
            if service.startswith(_REGEX_PREFIX):
                continue
            if not service.startswith(_MPRIS_SERVICE_PREFIX):
                self.services[i] = f"{_MPRIS_SERVICE_PREFIX}{service}"

    def first_available_service(self) -> str:
        available_services: list[str] = self.busctl.list_services()
        for service in self.services:
            pattern = self._service_pattern(service)
            for name in available_services:
                if pattern.fullmatch(name) and self._is_candidate_service(
                    name
                ):
                    return name
        raise ValueError("No matching service found.")

    def _service_pattern(self, service: str) -> re.Pattern[str]:
        if service.startswith(_REGEX_PREFIX):
            pattern_text = (
                f"{re.escape(_MPRIS_SERVICE_PREFIX)}"
                f"{service[len(_REGEX_PREFIX) :]}"
            )
            return re.compile(pattern_text)
        if "*" in service:
            pattern_text = ".*".join(
                re.escape(value) for value in service.split("*")
            )
            return re.compile(pattern_text)
        # Treat exact service names as matching instance-suffixed variants.
        # Example: org.mpris.MediaPlayer2.mpv.instance123.
        return re.compile(rf"{re.escape(service)}(?:\..+)?")

    def _is_candidate_service(self, service: str) -> bool:
        try:
            if not self._service_is_mpv(service):
                return True
            return not self._is_mpv_idle(service)
        except Exception as ex:
            logger.debug(
                "Candidate check failed for %s; using it", service, exc_info=ex
            )
            return True

    def _service_is_mpv(self, service: str) -> bool:
        try:
            identity = self.busctl.get_properties(
                service=service,
                object_path=self.object_path,
                interface=_MPRIS_ROOT_INTERFACE,
                properties=["Identity"],
            )[0]
        except Exception as ex:
            logger.debug(
                "Failed to read MPRIS identity for %s", service, exc_info=ex
            )
            return False
        return str(identity).strip().lower() == "mpv"

    def _is_mpv_idle(self, service: str) -> bool:
        try:
            pid = self.busctl.get_connection_unix_process_id(service)
        except Exception as ex:
            logger.debug(
                "Failed to get pid for service %s", service, exc_info=ex
            )
            return False

        socket_path = self._get_mpv_ipc_socket_path(pid)
        if socket_path is None:
            logger.debug(
                "Skipping idle check for %s (pid=%s): no ipc socket",
                service,
                pid,
            )
            return False

        idle_active = self._get_mpv_idle_active(socket_path)
        if idle_active is None:
            logger.debug(
                "Skipping idle check for %s (pid=%s): ipc query failed",
                service,
                pid,
            )
            return False

        if idle_active:
            logger.info("Ignoring idle mpv service: %s", service)
        return idle_active

    def _get_mpv_ipc_socket_path(self, pid: int) -> str | None:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        try:
            values = cmdline_path.read_bytes().split(b"\0")
        except OSError as ex:
            logger.debug("Failed to read cmdline for pid %s", pid, exc_info=ex)
            return None

        prefix = b"--input-ipc-server="
        for value in values:
            if value.startswith(prefix):
                socket_path = value[len(prefix) :].decode(errors="replace")
                if socket_path.startswith("@"):
                    return f"\0{socket_path[1:]}"
                return socket_path
        return None

    def _get_mpv_idle_active(self, socket_path: str) -> bool | None:
        payload = {"command": ["get_property", "idle-active"], "request_id": 1}
        response = self._query_mpv_ipc(socket_path, payload)
        if response is None:
            return None

        if response.get("error") != "success":
            return None

        value = response.get("data")
        if not isinstance(value, bool):
            return None
        return value

    def _query_mpv_ipc(
        self, socket_path: str, payload: dict[str, object]
    ) -> dict[str, object] | None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.75)
                client.connect(socket_path)
                client.sendall((json.dumps(payload) + "\n").encode())
                line = self._receive_json_line(client)
        except OSError as ex:
            logger.debug(
                "Failed to query mpv ipc at %r", socket_path, exc_info=ex
            )
            return None

        if line is None:
            return None

        try:
            decoded: object = json.loads(line.decode())
        except json.JSONDecodeError:
            logger.exception("Invalid mpv ipc response: %r", line)
            return None
        if not isinstance(decoded, dict):
            logger.debug("Unexpected mpv ipc response type: %r", decoded)
            return None
        response: dict[str, object] = {}
        for key, value in decoded.items():
            if not isinstance(key, str):
                logger.debug("Unexpected mpv ipc response keys: %r", decoded)
                return None
            response[key] = value
        return response

    def _receive_json_line(self, client: socket.socket) -> bytes | None:
        data = b""
        while b"\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            return None
        return data.split(b"\n", 1)[0]

    def get_properties(self, properties: list[str]) -> list[DBusValue]:
        return self.busctl.get_properties(
            service=self.first_available_service(),
            object_path=self.object_path,
            interface=self.interface,
            properties=properties,
        )

    def set_property(self, property: str, value: DBusValue) -> None:
        self.busctl.set_property(
            service=self.first_available_service(),
            object_path=self.object_path,
            interface=self.interface,
            property=property,
            value=value,
        )

    def call(
        self, method: str, arguments: list[DBusValue] | None = None
    ) -> list[DBusValue]:
        return self.busctl.call(
            service=self.first_available_service(),
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

    def seek(self, *, microseconds: int) -> None:
        self.call("Seek", [DBusValue.from_int(microseconds, bits_64=True)])

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
        description="Uses dbus to control media players."
    )
    add_log_arguments(parser)
    parser.add_argument(
        "services",
        nargs="+",
        help=(
            "The service names to try to match, in order of preference."
            "  Wildcards are supported via '*' characters."
            "  Exact names also match instance suffixes, so 'mpv' matches"
            " both org.mpris.MediaPlayer2.mpv and"
            " org.mpris.MediaPlayer2.mpv.instance-..."
            "  Use 're:<pattern>' for regex mode, e.g."
            " 're:mpv(\\..*)?'."
            "  In regex mode, the pattern applies after"
            " 'org.mpris.MediaPlayer2.'."
        ),
    )
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
    operation_group.add_argument(
        "--seek-ahead", action="store_true", help="Seeks ahead 5 seconds"
    )
    operation_group.add_argument(
        "--seek-back", action="store_true", help="Seeks back 5 seconds"
    )
    args = parser.parse_args(args=argv)
    configure_logging(args)
    media_control = MediaControl(services=args.services)
    if args.volume_set is not None:
        media_control.volume_set(args.volume_set)
    if args.volume_adjust is not None:
        media_control.volume_adjust(args.volume_adjust)
    if args.play:
        media_control.play()
    if args.pause:
        media_control.pause()
    if args.play_toggle:
        media_control.play_toggle()
    if args.stop:
        media_control.stop()
    if args.next:
        media_control.next()
    if args.previous:
        media_control.previous()
    FIVE_SECONDS = 5000000  # in microseconds; chromium only seeks 5s anyway.
    if args.seek_ahead:
        media_control.seek(microseconds=FIVE_SECONDS)
    if args.seek_back:
        media_control.seek(microseconds=-FIVE_SECONDS)
    return 0
