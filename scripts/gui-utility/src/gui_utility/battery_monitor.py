from __future__ import annotations

import argparse
import logging
import signal
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageDraw

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


def generate_battery_pixbuf(
    percent: int,
    size: int = 64,
    gap_units: int = 1,
    rounded: bool = True,
    show_nub: bool = True,
) -> GdkPixbuf.Pixbuf:
    percent = max(0, min(percent, 100))
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    unit = size // 16
    border_width = unit * 2
    width = (size * 3) // 4
    padding = (size - width) // 2
    border_color = "black"

    border = Rectangle(left=padding, right=padding + width, top=0, bottom=size)

    if show_nub:
        nub_width = border_width * 2
        nub_padding = (size - nub_width) // 2
        nub = Rectangle(
            left=nub_padding,
            right=nub_padding + nub_width,
            top=0,
            bottom=border_width * 3 // 2,  # overlaps actual border on purpose
        )
        nub.draw(
            draw, fill=border_color, radius=border_width // 2 if rounded else 0
        )
        border = border.offset(top=border_width)

    border.draw(
        draw,
        outline=border_color,
        width=border_width,
        radius=border_width * 3 // 2 if rounded else 0,
    )

    # Fill color based on percent
    def color(p: int) -> tuple[int, int, int]:
        if p >= 80:
            return (0, 200, 0)
        elif p >= 60:
            return (128, 200, 0)
        elif p >= 40:
            return (200, 200, 0)
        elif p >= 20:
            return (200, 128, 0)
        else:
            return (200, 0, 0)

    inner_rectangle = border.offset_edges(border_width + gap_units * unit)
    inner_rectangle.top += inner_rectangle.height * (100 - percent) // 100
    inner_rectangle.draw(draw, fill=color(percent))

    return pil_to_pixbuf(img)


def on_quit(menu_item: Gtk.MenuItem) -> None:
    Gtk.main_quit()


def on_ctrl_c(sig: int, frame: types.FrameType | None) -> None:
    Gtk.main_quit()


def on_right_click(icon: Gtk.StatusIcon, button: int, time: int) -> None:
    menu = Gtk.Menu()

    item_quit = Gtk.MenuItem(label="Quit")
    item_quit.connect("activate", on_quit)
    menu.append(item_quit)

    menu.show_all()
    menu.popup_at_pointer(None)


def update_battery_indicator(icon: Gtk.StatusIcon) -> bool:
    # TODO: don't regenerate the image every single time, and perhaps use some
    # threshold like 5% instead of every single %
    capacity = int(Path("/sys/class/power_supply/BAT0/capacity").read_text())
    icon.set_from_pixbuf(generate_battery_pixbuf(capacity))
    icon.set_tooltip_text(f"Battery: {capacity}%")
    print(f"Current capacity: {capacity}")
    return True  # Return True to keep repeating, False to stop


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitors the battery status")
    add_log_arguments(parser)
    args = parser.parse_args(args=argv)
    configure_logging(args)

    icon = Gtk.StatusIcon()
    # icon.set_from_icon_name("battery")
    icon.set_visible(True)
    icon.connect("popup-menu", on_right_click)
    signal.signal(signal.SIGINT, on_ctrl_c)
    update_handler: Callable[[], bool] = lambda: update_battery_indicator(icon)
    update_handler()
    GLib.timeout_add(5000, update_handler)
    Gtk.main()
    return 0
