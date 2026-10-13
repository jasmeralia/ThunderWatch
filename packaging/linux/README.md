# Linux packages

`build-packages.sh OUTPUT_DIR` builds DEB, RPM, AppImage, Flatpak, and Snap artifacts for
the current amd64 or arm64 host. It requires PyInstaller, dpkg-deb, rpmbuild, Flatpak,
Snapcraft, appimagetool, and an AppImage runtime file supplied through
`APPIMAGE_RUNTIME_FILE`. CI verifies checksums and installs/smoke-tests package outputs.

Flatpak grants network, Wayland/X11, StatusNotifierWatcher, and Secret Service access;
it receives no filesystem grants. Snap connects the password-manager service through
`sudo snap connect thunderwatch:password-manager-service` when the host has not
connected it automatically.
