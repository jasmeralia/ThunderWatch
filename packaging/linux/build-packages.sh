#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTPUT_DIR" >&2
    exit 2
fi

output_dir=$(mkdir -p "$1" && cd "$1" && pwd)
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
version=${APP_VERSION:-0.0.0}
version_bare=${version#v}
arch=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$arch" in
    x86_64|amd64) arch=amd64; flatpak_arch=x86_64; rpm_arch=x86_64 ;;
    aarch64|arm64) arch=arm64; flatpak_arch=aarch64; rpm_arch=aarch64 ;;
    *) echo "unsupported architecture: $arch" >&2; exit 2 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
app=$work/app
mkdir -p "$app/usr/bin" "$app/usr/share/applications" \
    "$app/usr/share/icons/hicolor/256x256/apps" "$app/usr/share/doc/thunderwatch"

python -m PyInstaller "$repo_root/build/ThunderWatch.spec" \
    --noconfirm --clean --distpath "$work/dist" --workpath "$work/pyinstaller"
install -m 0755 "$work/dist/ThunderWatch" "$app/usr/bin/thunderwatch"
install -m 0644 "$repo_root/packaging/linux/thunderwatch.desktop" \
    "$app/usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop"
install -m 0644 "$repo_root/resources/icons/thunderwatch.png" \
    "$app/usr/share/icons/hicolor/256x256/apps/io.github.jasmeralia.ThunderWatch.png"
install -m 0644 "$repo_root/LICENSE" "$app/usr/share/doc/thunderwatch/copyright"

build_deb() {
    local root=$work/deb
    mkdir -p "$root/DEBIAN"
    cp -a "$app/." "$root/"
    cat > "$root/DEBIAN/control" <<EOF
Package: thunderwatch
Version: ${version#v}
Section: net
Priority: optional
Architecture: $arch
Maintainer: ThunderWatch contributors
Depends: libc6 (>= 2.39), libx11-6, libxcb1, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xinerama0, libxcb-xkb1, libegl1, libgl1
Recommends: libwayland-cursor0, libwayland-egl1
Description: Public IP address change monitor
 Monitors public IP addresses and emails confirmed changes.
EOF
    dpkg-deb --root-owner-group --build "$root" \
        "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.deb"
}

build_rpm() {
    local top=$work/rpmbuild
    mkdir -p "$top"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    mkdir -p "$top/SOURCES/thunderwatch-rootfs"
    cp -a "$app/." "$top/SOURCES/thunderwatch-rootfs/"
    cat > "$top/SPECS/thunderwatch.spec" <<EOF
Name:           thunderwatch
Version:        ${version#v}
Release:        1
Summary:        Public IP address change monitor
License:        GPL-3.0-or-later
BuildArch:      $rpm_arch
Requires:       glibc >= 2.39
Requires:       libX11
Requires:       libxcb
Requires:       libxkbcommon-x11
Requires:       xcb-util-cursor
Requires:       xcb-util-wm
Requires:       xcb-util-image
Requires:       xcb-util-keysyms
Requires:       xcb-util-renderutil
Requires:       libglvnd-glx
Requires:       libglvnd-egl
Recommends:     libwayland-cursor
Recommends:     libwayland-egl
%description
Monitors public IP addresses and emails confirmed changes.
%install
mkdir -p %{buildroot}
cp -a "$top/SOURCES/thunderwatch-rootfs/." %{buildroot}/
%files
/usr/bin/thunderwatch
/usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop
/usr/share/icons/hicolor/256x256/apps/io.github.jasmeralia.ThunderWatch.png
/usr/share/doc/thunderwatch/copyright
EOF
    rpmbuild --define "_topdir $top" -bb "$top/SPECS/thunderwatch.spec"
    cp "$top/RPMS/$rpm_arch/"*.rpm \
        "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.rpm"
}

build_appimage() {
    if [[ -z "${APPIMAGE_RUNTIME_FILE:-}" || ! -f "$APPIMAGE_RUNTIME_FILE" ]]; then
        echo "APPIMAGE_RUNTIME_FILE must name the verified AppImage runtime file" >&2
        return 1
    fi
    local dir=$work/AppDir
    mkdir -p "$dir/usr/bin" "$dir/usr/share/applications" \
        "$dir/usr/share/icons/hicolor/256x256/apps"
    cp -a "$app/usr/bin/thunderwatch" "$dir/usr/bin/"
    sed 's/^Exec=thunderwatch$/Exec=AppRun/' \
        "$app/usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop" \
        > "$dir/usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop"
    cp -a "$app/usr/share/icons/hicolor/256x256/apps/"* \
        "$dir/usr/share/icons/hicolor/256x256/apps/"
    ln -s usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop "$dir/thunderwatch.desktop"
    ln -s usr/share/icons/hicolor/256x256/apps/io.github.jasmeralia.ThunderWatch.png \
        "$dir/io.github.jasmeralia.ThunderWatch.png"
    cat > "$dir/AppRun" <<'EOF'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$HERE/usr/bin/thunderwatch" "$@"
EOF
    chmod 0755 "$dir/AppRun"
    local appimage_arch=x86_64
    [[ "$arch" == arm64 ]] && appimage_arch=aarch64
    APPIMAGE_EXTRACT_AND_RUN=1 VERSION="$version" ARCH="$appimage_arch" appimagetool \
        --runtime-file "$APPIMAGE_RUNTIME_FILE" "$dir" \
        "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.AppImage"
    chmod 0755 "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.AppImage"
}

build_flatpak() {
    command -v flatpak >/dev/null
    flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install --user --noninteractive --assumeyes flathub \
        "org.freedesktop.Platform/$flatpak_arch/26.08" \
        "org.freedesktop.Sdk/$flatpak_arch/26.08"
    local dir=$work/flatpak-build
    local repo=$work/flatpak-repo
    flatpak build-init --arch="$flatpak_arch" "$dir" \
        io.github.jasmeralia.ThunderWatch org.freedesktop.Sdk org.freedesktop.Platform 26.08
    install -D -m 0755 "$app/usr/bin/thunderwatch" "$dir/files/bin/thunderwatch"
    install -D -m 0644 "$app/usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop" \
        "$dir/files/share/applications/io.github.jasmeralia.ThunderWatch.desktop"
    install -D -m 0644 "$app/usr/share/icons/hicolor/256x256/apps/io.github.jasmeralia.ThunderWatch.png" \
        "$dir/files/share/icons/hicolor/256x256/apps/io.github.jasmeralia.ThunderWatch.png"
    flatpak build-finish --command=thunderwatch --socket=wayland --socket=fallback-x11 \
        --share=network --talk-name=org.kde.StatusNotifierWatcher \
        --talk-name=org.freedesktop.secrets "$dir"
    flatpak build-export --arch="$flatpak_arch" "$repo" "$dir" stable
    flatpak build-bundle --arch="$flatpak_arch" "$repo" \
        "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.flatpak" \
        io.github.jasmeralia.ThunderWatch stable \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
}

build_snap() {
    local dir=$work/snap
    mkdir -p "$dir/app"
    cp -a "$app/." "$dir/app/"
    cat > "$dir/snapcraft.yaml" <<EOF
name: thunderwatch
base: core24
version: '${version#v}'
summary: Public IP address change monitor
description: Monitors public IP addresses and emails confirmed changes.
grade: devel
confinement: strict
platforms:
  $arch:
    build-on: [$arch]
    build-for: [$arch]
apps:
  thunderwatch:
    command: usr/bin/thunderwatch
    desktop: usr/share/applications/io.github.jasmeralia.ThunderWatch.desktop
    plugs: [network, desktop, wayland, x11, password-manager-service]
    autostart: thunderwatch.desktop
parts:
  app:
    plugin: dump
    source: app
    stage-packages:
      - libx11-6
      - libxcb1
      - libxkbcommon-x11-0
      - libxcb-cursor0
      - libxcb-icccm4
      - libxcb-image0
      - libxcb-keysyms1
      - libxcb-randr0
      - libxcb-render-util0
      - libxcb-xinerama0
      - libxcb-xkb1
      - libegl1
      - libgl1
      - libfontconfig1
      - libxcb-shape0
      - libwayland-cursor0
      - libwayland-egl1
EOF
    (cd "$dir" && snapcraft pack --destructive-mode --output \
        "$output_dir/ThunderWatch-v${version_bare}-linux-${arch}.snap")
}

build_deb
build_rpm
build_appimage
build_flatpak
build_snap

for package in "$output_dir"/*; do
    case "$package" in
        *.deb) dpkg-deb --info "$package" >/dev/null ;;
        *.rpm) rpm -qp --info "$package" >/dev/null ;;
        *.AppImage) test -x "$package" ;;
        *.flatpak) flatpak build-bundle --help >/dev/null ;;
        *.snap) unsquashfs -s "$package" >/dev/null ;;
    esac
done
