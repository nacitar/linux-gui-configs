from __future__ import annotations

import argparse
import colorsys
import logging
import signal
import types
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

from .dbus import BusCtl
from .gi import GdkPixbuf, GLib, Gtk
from .log_utility import add_log_arguments, configure_logging

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Rectangle:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def components(self) -> list[int]:
        return [self.left, self.top, self.right - 1, self.bottom - 1]

    @property
    def width(self) -> int:
        return max(max(self.left, self.right) - min(self.left, self.right), 0)

    @property
    def height(self) -> int:
        return max(max(self.top, self.bottom) - min(self.top, self.bottom), 0)

    @property
    def area(self) -> int:
        return self.width * self.height

    def offset(
        self, *, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0
    ) -> Rectangle:
        return Rectangle(
            left=self.left + left,
            top=self.top + top,
            right=self.right + right,
            bottom=self.bottom + bottom,
        )

    def offset_edges(self, amount: int) -> Rectangle:
        return Rectangle(
            left=self.left + amount,
            top=self.top + amount,
            right=self.right - amount,
            bottom=self.bottom - amount,
        )

    def draw(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        radius: int = 0,
        width: int = 1,
        fill: (
            str | tuple[int, int, int] | tuple[int, int, int, int] | None
        ) = None,
        outline: (
            str | tuple[int, int, int] | tuple[int, int, int, int] | None
        ) = None,
    ) -> None:
        if self.area:
            if not radius:
                draw.rectangle(
                    self.components, fill=fill, outline=outline, width=width
                )
            else:
                draw.rounded_rectangle(
                    self.components,
                    radius=radius,
                    fill=fill,
                    outline=outline,
                    width=width,
                )


def pil_to_pixbuf(pil_image: Image.Image) -> GdkPixbuf.Pixbuf:
    """Convert a PIL Image to GdkPixbuf.Pixbuf."""
    if pil_image.mode != "RGBA":
        pil_image = pil_image.convert("RGBA")
    data = pil_image.tobytes()
    width, height = pil_image.size
    rowstride = width * 4
    return GdkPixbuf.Pixbuf.new_from_data(
        data,
        GdkPixbuf.Colorspace.RGB,
        True,  # has_alpha
        8,  # bits per sample
        width,
        height,
        rowstride,
    )


def clamp_percent(percent: int) -> int:
    return max(0, min(percent, 100))


@dataclass(kw_only=True)
class IconStyle:
    show_nub: bool
    rounded: bool
    connected_border_color: str = "black"
    disconnected_border_color: str = "yellow"
    size: int = 64
    gap_units: int = 1


class BatteryLevel(StrEnum):
    HIGH = "battery-full"
    GOOD = "battery-good"
    LOW = "battery-low"
    CAUTION = "battery-caution"
    # battery-empty

    @property
    def icon(self) -> str:
        return self.value


@dataclass
class BatteryMonitor:
    style: IconStyle
    battery_path: Path
    capacity: int = 0
    connected: bool = True
    busctl: BusCtl = field(default_factory=BusCtl)

    icon: Gtk.StatusIcon = field(init=False)

    def _get_render_category(self) -> int:
        return self.capacity // 5

    def _get_level(self) -> BatteryLevel:
        if self.capacity > 90:
            return BatteryLevel.HIGH
        if self.capacity > 50:
            return BatteryLevel.GOOD
        if self.capacity > 10:
            return BatteryLevel.LOW
        return BatteryLevel.CAUTION

    def __post_init__(self) -> None:
        icon = Gtk.StatusIcon()
        # icon.set_from_icon_name("battery")
        icon.set_visible(True)
        icon.connect(
            "popup-menu",
            lambda icon, button, time: self._on_right_click(
                icon, button, time
            ),
        )
        self.icon = icon
        self.update()

    def _on_right_click(
        self, icon: Gtk.StatusIcon, button: int, time: int
    ) -> None:
        menu = Gtk.Menu()
        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect(
            "activate", lambda menu_item: self._on_quit(menu_item)
        )
        menu.append(item_quit)
        menu.show_all()
        menu.popup_at_pointer(None)

    @staticmethod
    def quit() -> None:
        Gtk.main_quit()

    def _on_quit(self, menu_item: Gtk.MenuItem) -> None:
        self.quit()

    def _on_sigint(self, sig: int, frame: types.FrameType | None) -> None:
        self.quit()

    def run(self, update_ms: int = 60000) -> None:
        GLib.timeout_add(update_ms, lambda: self.update())
        Gtk.main()

    @classmethod
    def power_gradient_color(cls, percent: int) -> tuple[int, int, int]:
        """
        Interpolate from green to red in HSV color space and return an
        tuple of RGB values.  100% => green (120°), 0% => red (0°), goes
        through yellow and orange.
        """
        percent = clamp_percent(percent)
        hue_deg = (percent / 100) * 120  # 0 = red, 60 = yellow, 120 = green
        hue = hue_deg / 360  # convert to 0–1 for colorsys
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        return (int(r * 255), int(g * 255), int(b * 255))

    def update(self) -> bool:
        old_render_category = self._get_render_category()
        old_level = self._get_level()
        old_capacity = self.capacity
        self.capacity = clamp_percent(
            int(
                (self.battery_path / "capacity")
                .read_text(encoding="utf-8")
                .strip()
            )
        )
        render_category = self._get_render_category()
        level = self._get_level()
        old_connected = self.connected
        status = (
            (self.battery_path / "status").read_text(encoding="utf-8").strip()
        )
        self.connected = (
            status in ["Charging", "Full"]
            or status == "Not charging"
            and self.capacity > 75
        )
        set_new_state = False
        set_new_icon = False
        send_notification = False
        if self.capacity != old_capacity:
            logger.info(f"new battery capacity: {self.capacity}")
            set_new_state = True
            if render_category != old_render_category:
                logger.info(f"new battery render category: {render_category}")
                set_new_icon = True
            if level != old_level and (
                not old_capacity or max(old_capacity, self.capacity) != 100
            ):
                send_notification = True
        else:
            logger.debug(f"unchanged battery capacity: {self.capacity}")
        if self.connected != old_connected:
            logger.debug(f"charging state changed: {status}")
            set_new_icon = True
            set_new_state = True
            send_notification = True
        if set_new_icon:
            self.icon.set_from_pixbuf(
                self._render_pixbuf(self.capacity, connected=self.connected)
            )
        state_text = "\n".join(
            [
                f"Charge: {self.capacity}%",
                f"Power: {"dis" if not self.connected else ""}connected",
            ]
        )
        if set_new_state:
            self.icon.set_tooltip_text(state_text)

        if send_notification:
            self.busctl.notify(
                application_name="battery-monitor",
                icon=level.icon,
                title=f"Battery Level - {level.name}",
                body=state_text,
            )
        return True  # keep running

    def _render_pixbuf(
        self, percent: int, connected: bool
    ) -> GdkPixbuf.Pixbuf:
        percent = clamp_percent(percent)
        img = Image.new(
            "RGBA", (self.style.size, self.style.size), (255, 255, 255, 0)
        )
        draw = ImageDraw.Draw(img)

        unit = self.style.size // 16
        border_width = unit * 2
        width = (self.style.size * 3) // 4
        padding = (self.style.size - width) // 2

        border = Rectangle(
            left=padding, right=padding + width, top=0, bottom=self.style.size
        )

        if self.style.show_nub:
            nub_width = border_width * 2
            nub_padding = (self.style.size - nub_width) // 2
            nub = Rectangle(
                left=nub_padding,
                right=nub_padding + nub_width,
                top=0,
                bottom=border_width
                * 3
                // 2,  # overlaps actual border on purpose
            )
            nub.draw(
                draw,
                fill=(
                    self.style.connected_border_color
                    if connected
                    else self.style.disconnected_border_color
                ),
                radius=border_width // 2 if self.style.rounded else 0,
            )
            border = border.offset(top=border_width)

        border.draw(
            draw,
            outline=(
                self.style.connected_border_color
                if connected
                else self.style.disconnected_border_color
            ),
            width=border_width,
            radius=border_width * 3 // 2 if self.style.rounded else 0,
        )

        inner_rectangle = border.offset_edges(
            border_width + self.style.gap_units * unit
        )
        inner_rectangle.top += inner_rectangle.height * (100 - percent) // 100
        inner_rectangle.draw(
            draw, fill=BatteryMonitor.power_gradient_color(percent)
        )

        return pil_to_pixbuf(img)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitors the battery status")
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)

    # TODO: add arguments to customize things, including the capacity source
    monitor = BatteryMonitor(
        battery_path=Path("/sys/class/power_supply/BAT0"),
        style=IconStyle(
            show_nub=True,
            rounded=True,
            connected_border_color="rgb(211,215,207)",
            disconnected_border_color="rgb(255,255,0)",
        ),
    )
    signal.signal(signal.SIGINT, monitor._on_sigint)
    monitor.run(update_ms=5000)
    return 0
