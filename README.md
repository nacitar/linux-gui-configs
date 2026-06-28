# linux-gui-configs
Holds configs for window managers, terminals, and other things GUI.

This installs my settings for the user's Xorg related files and fluxbox.

This WILL overwrite your local setup.

Installation:
```bash
git clone https://github.com/nacitar/linux-gui-configs.git "${HOME}/.gui"
"${HOME}/.gui/install.sh"
```

# Dependencies

| Dependency | Enables |
| --- | --- |
| `fluxbox` | Window manager |
| `picom` | Compositing |
| `dunst` | Notifications |
| `snixembed` | Modern (StatusNotifier) tray icon support |
| `blueman` | Bluetooth applet |
| `playerctl` | Media key control |
| `av-output-switcher` | Display, audio, and primary-monitor output switching |
| `battery-tray` | Battery status in the system tray |
| `feh` | Wallpaper setting. |

`av-output-switcher` and `battery-tray` are Python tools installed with `uv`;
the rest are ArchLinux packages. `av-output-switcher` also shells out to
`xrandr`, `xprop`, and `pactl` (the `xorg-xrandr`, `xorg-xprop`, and
`libpulse` packages).

Installation (ArchLinux):
```bash
pacman -S fluxbox picom dunst snixembed blueman playerctl feh \
    xorg-xrandr xorg-xprop libpulse
uv tool install av-output-switcher battery-tray
```
