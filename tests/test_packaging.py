from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_flatpak_manifest_grants_only_documented_permissions():
    manifest = (ROOT / "packaging/linux/io.github.jasmeralia.ThunderWatch.yml").read_text()
    grants = [line.strip() for line in manifest.splitlines() if line.strip().startswith("- --")]
    assert grants == [
        "- --share=network",
        "- --socket=wayland",
        "- --socket=fallback-x11",
        "- --talk-name=org.kde.StatusNotifierWatcher",
        "- --talk-name=org.freedesktop.secrets",
    ]
    assert "filesystem" not in manifest


def test_nsis_uninstaller_removes_windows_autostart_registration():
    installer = (ROOT / "build/installer.nsi").read_text()
    uninstall = installer.split('Section "Uninstall"', 1)[1].split("SectionEnd", 1)[0]
    assert (
        'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "ThunderWatch"'
        in uninstall
    )


def test_linux_package_script_has_no_tempesttrace_product_references():
    script = (ROOT / "packaging/linux/build-packages.sh").read_text().casefold()
    assert "tempesttrace" not in script
    assert "obs-studio" not in script
    assert "dropbox" not in script
