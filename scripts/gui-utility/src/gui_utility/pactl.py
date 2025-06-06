from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .cli_tool import CLITool

logger = logging.getLogger(__name__)


@dataclass
class JsonReader:
    data: dict[Any, Any] | list[Any]

    def __len__(self) -> int:
        return len(self.data)

    def __get(self, key: Any, default: Any = None) -> Any:
        if isinstance(self.data, list):
            if not isinstance(key, int):
                raise AssertionError("indexes must be integral for lists.")
            value = self.data[key]
        else:
            value = self.data.get(key, default)
        return value

    def get_str(self, key: Any, default: str = "") -> str:
        value = self.__get(key, default)
        if not isinstance(value, str):
            raise TypeError(
                f'value for key "{key}" expected to be str'
                f" but is: {type(value).__name__}"
            )
        return value

    def get_int(self, key: Any, default: int = 0) -> int:
        value = self.__get(key, default)
        if not isinstance(value, int):
            raise TypeError(
                f'value for key "{key}" expected to be int'
                f" but is: {type(value).__name__}"
            )
        return value

    def get_section(
        self, key: Any, default: dict[Any, Any] | None = None
    ) -> JsonReader:
        value = self.__get(key, default)
        if not isinstance(value, dict):
            raise TypeError(
                f'value for key "{key}" expected to be dict'
                f" but is: {type(value).__name__}"
            )
        return JsonReader(value)


@dataclass
class PACtl:
    tool: CLITool = field(default_factory=lambda: CLITool("pactl"))

    def get_sinks(self) -> list[Sink]:
        reader = JsonReader(
            json.loads(self.tool.invoke(["-f", "json", "list", "sinks"]))
        )
        sinks: list[Sink] = []
        for i in range(len(reader)):
            sink_reader = reader.get_section(i)
            name = sink_reader.get_str("name")

            properties = sink_reader.get_section("properties")
            alsa_card_number = properties.get_str("alsa.card")
            if not alsa_card_number:
                logger.warning(f"Skipping non-alsa sink: {name}")
                continue
            sink = Sink(
                name=name,
                monitor_source_name=sink_reader.get_str("monitor_source"),
                alsa_card_number=int(alsa_card_number),
            )
            # "mute" and "volume" properties appear to refer to the right value
            sinks.append(sink)
        return sinks

    def get_default_sink_name(self) -> str:
        return self.tool.invoke(["get-default-sink"])

    def set_default_sink(self, sink: Sink | str) -> None:
        if isinstance(sink, Sink):
            sink = sink.name
        logger.info(f"setting default PulseAudio sink to {sink}")
        self.tool.invoke(["set-default-sink", sink])

    def cycle_default_sink(self) -> None:
        sinks = self.get_sinks()
        default_sink_name = self.get_default_sink_name()
        selected_index = next(
            (
                i
                for i, sink in enumerate(sinks)
                if sink.name == default_sink_name
            ),
            -1,
        )
        next_sink = sinks[(selected_index + 1) % len(sinks)]
        logger.info(
            f"cycling PulseAudio from {default_sink_name} to {next_sink}"
        )
        self.set_default_sink(next_sink.name)


@dataclass(frozen=True, order=True)
class Sink:
    name: str
    monitor_source_name: str
    alsa_card_number: int
