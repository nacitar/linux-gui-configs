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
For the battery-monitor to function, certain system dependencies are required.
For ArchLinux, install them via:
```
pacman -S gtk3 gobject-introspection libgdk-pixbuf2
```
