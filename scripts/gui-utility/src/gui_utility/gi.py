import gi  # type: ignore

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # type: ignore  # noqa: E402

__all__ = ["GdkPixbuf", "GLib", "Gtk"]
