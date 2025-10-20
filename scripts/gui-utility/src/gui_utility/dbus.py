from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .cli_tool import CLITool

logger = logging.getLogger(__name__)


@dataclass
class DBusValue:
    type: str
    value: Any

    @classmethod
    def from_str_array(cls, value: list[str]) -> DBusValue:
        return cls(type="as", value=value)

    def to_str_array(self) -> list[str]:
        if self.type == "as":
            if (
                isinstance(self.value, list)
                and len(self.value) == 1
                and isinstance(self.value[0], list)
            ):
                return list(self.value[0])
        raise ValueError(f"not a list[str] type: {self.type}")

    @classmethod
    def from_str_dict(cls, value: dict[str, Any]) -> DBusValue:
        return cls(type="a{sv}", value=value)

    def to_str_dict(self) -> dict[str, Any]:
        if self.type == "a{sv}":
            return dict(self.value)
        raise ValueError(f"not a dict[str, Any] type: {self.type}")

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

    def value_arguments(self) -> list[str]:
        if isinstance(self.value, list):
            return [str(len(self.value))] + [
                str(value) for value in self.value
            ]
        if isinstance(self.value, dict):
            return [str(len(self.value))] + [
                item for pair in self.value.items() for item in pair
            ]
        return [str(self.value)]

    def to_property_cli(self) -> list[str]:
        return [self.type] + self.value_arguments()


@dataclass
class BusCtl:
    tool: CLITool = field(default_factory=lambda: CLITool("busctl"))

    def invoke(self, arguments: list[str]) -> list[DBusValue]:
        return [
            DBusValue.from_json(line)
            for line in self.tool.invoke(
                ["--json=short", "--user", "--"] + arguments
            ).splitlines()
        ]

    def notify(
        self,
        *,
        application_name: str,
        title: str,
        body: str,
        timeout_ms: int = 5000,
        id: int = 0,
        icon: str = "",
    ) -> None:
        self.call(
            service="org.freedesktop.Notifications",
            object_path="/org/freedesktop/Notifications",
            interface="org.freedesktop.Notifications",
            method="Notify",
            arguments=[
                DBusValue.from_str(application_name),  # app name
                DBusValue.from_int(id, unsigned=True),  # id
                DBusValue.from_str(icon),  # icon
                DBusValue.from_str(title),  # summary/title
                DBusValue.from_str(body),  # body/message
                DBusValue.from_str_array([]),  # actions
                DBusValue.from_str_dict({}),  # hints map
                DBusValue.from_int(timeout_ms),  # timeout
            ],
        )

    def list_services(self, prefix: str = "") -> list[str]:
        values = self.call(
            service="org.freedesktop.DBus",
            object_path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            method="ListNames",
        )
        if len(values) != 1:
            raise ValueError(
                f"busctl had unexpected output listing services: {values}"
            )
        return values[0].to_str_array()

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
            + value.to_property_cli()
        )

    def call(
        self,
        service: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: list[DBusValue] | None = None,
    ) -> list[DBusValue]:
        cli_arguments: list[str] = []
        if arguments:
            types_string = ""
            for argument in arguments:
                types_string += argument.type
                cli_arguments.extend(argument.value_arguments())
            cli_arguments = [types_string] + cli_arguments
        return self.invoke(
            ["call", service, object_path, interface, method] + cli_arguments
        )
