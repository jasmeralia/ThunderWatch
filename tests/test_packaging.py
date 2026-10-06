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


def test_nsis_process_check_pops_both_ns_exec_results():
    installer = (ROOT / "build/installer.nsi").read_text()
    for section in ("Function KillRunningThunderWatch", "Function un.KillRunningThunderWatch"):
        function = installer.split(section, 1)[1].split("FunctionEnd", 1)[0]
        exec_call = function.index("nsExec::ExecToStack")
        return_code = function.index("Pop $1", exec_call)
        captured_output = function.index("Pop $2", exec_call)
        assert return_code < captured_output


def test_nsis_rollback_relaunches_restored_application():
    installer = (ROOT / "build/installer.nsi").read_text()
    failed_callback = installer.split("Function .onInstFailed", 1)[1].split("FunctionEnd", 1)[0]
    assert "Exec '\"$INSTDIR\\ThunderWatch.exe\" --show'" in failed_callback


def test_nsis_checks_recovery_directory_before_stopping_application():
    installer = (ROOT / "build/installer.nsi").read_text()
    section = installer.split('Section "ThunderWatch" SecMain', 1)[1].split("SectionEnd", 1)[0]
    assert section.index('IfFileExists "$INSTDIR\\.thunderwatch-upgrade-rollback"') < section.index(
        "Call KillRunningThunderWatch"
    )


def test_linux_package_script_has_no_tempesttrace_product_references():
    script = (ROOT / "packaging/linux/build-packages.sh").read_text().casefold()
    assert "tempesttrace" not in script
    assert "obs-studio" not in script
    assert "dropbox" not in script
