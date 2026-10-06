; Compile from the repository root after PyInstaller creates dist/ThunderWatch.exe:
; makensis /DAPP_VERSION=v0.1.0 build/installer.nsi
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_VERSION
  !error "Pass /DAPP_VERSION=<release tag> to makensis"
!endif

Unicode True
Name "ThunderWatch ${APP_VERSION}"
OutFile "..\dist\ThunderWatch-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\ThunderWatch"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ICON "..\resources\icons\thunderwatch.ico"
!define MUI_UNICON "..\resources\icons\thunderwatch.ico"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\ThunderWatch.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ThunderWatch"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!macro BackupOwnedFile FILENAME
  IfFileExists "$INSTDIR\${FILENAME}" 0 +3
    Rename "$INSTDIR\${FILENAME}" "$INSTDIR\.thunderwatch-upgrade-rollback\${FILENAME}"
    IfErrors install_failed
!macroend

!macro RemoveRollbackFile FILENAME
  Delete "$INSTDIR\.thunderwatch-upgrade-rollback\${FILENAME}"
!macroend

!macro RestoreOwnedFile FILENAME FLAG LABEL
  IfFileExists "$INSTDIR\.thunderwatch-upgrade-rollback\${FILENAME}" restore_${LABEL} no_backup_${LABEL}
  restore_${LABEL}:
    Delete "$INSTDIR\${FILENAME}"
    Rename "$INSTDIR\.thunderwatch-upgrade-rollback\${FILENAME}" "$INSTDIR\${FILENAME}"
    Goto restored_${LABEL}
  no_backup_${LABEL}:
    ${If} ${FLAG} == "1"
      Delete "$INSTDIR\${FILENAME}"
    ${EndIf}
  restored_${LABEL}:
!macroend

Section "ThunderWatch" SecMain
  SectionIn RO
  Call KillRunningThunderWatch
  StrCpy $R7 "0"
  StrCpy $R4 "0"
  StrCpy $R5 "0"
  StrCpy $R6 "0"

  ; Keep only the files owned by this installer in a same-volume rollback
  ; directory. Arbitrary files in $INSTDIR are never moved or removed.
  IfFileExists "$INSTDIR\.thunderwatch-upgrade-rollback" rollback_exists
  CreateDirectory "$INSTDIR\.thunderwatch-upgrade-rollback"
  IfErrors prepare_failed
  StrCpy $R7 "1"
  !insertmacro BackupOwnedFile "ThunderWatch.exe"
  !insertmacro BackupOwnedFile "LICENSE"
  !insertmacro BackupOwnedFile "Uninstall.exe"

  SetOutPath "$INSTDIR"
  IfErrors install_failed
  StrCpy $R4 "1"
  File "..\dist\ThunderWatch.exe"
  IfErrors install_failed
  StrCpy $R5 "1"
  File "..\LICENSE"
  IfErrors install_failed
  StrCpy $R6 "1"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors install_failed

  ; Validate the replacement executable before removing rollback copies.
  ClearErrors
  ExecWait '"$INSTDIR\ThunderWatch.exe" --smoke-test' $0
  IfErrors install_failed
  ${If} $0 != 0
    Goto install_failed
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\ThunderWatch"
  CreateShortCut "$SMPROGRAMS\ThunderWatch\ThunderWatch.lnk" "$INSTDIR\ThunderWatch.exe"
  CreateShortCut "$SMPROGRAMS\ThunderWatch\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" \
    "DisplayName" "ThunderWatch"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" \
    "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" \
    "Publisher" "Morgan Blackthorne"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" \
    "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch" \
    "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'

  !insertmacro RemoveRollbackFile "ThunderWatch.exe"
  !insertmacro RemoveRollbackFile "LICENSE"
  !insertmacro RemoveRollbackFile "Uninstall.exe"
  RMDir "$INSTDIR\.thunderwatch-upgrade-rollback"
  Goto install_done

  rollback_exists:
    MessageBox MB_ICONSTOP|MB_OK "A previous ThunderWatch upgrade left recovery files at:$\r$\n$INSTDIR\.thunderwatch-upgrade-rollback$\r$\nRestore or remove that directory before running the installer again."
    Abort
  prepare_failed:
    MessageBox MB_ICONSTOP|MB_OK "Could not prepare the ThunderWatch upgrade safely. The existing installation was left untouched."
    Abort
  install_failed:
    MessageBox MB_ICONSTOP|MB_OK "ThunderWatch could not be installed. The installer will restore the previous files where possible."
    Abort
  install_done:
SectionEnd

Section "Uninstall"
  Call un.KillRunningThunderWatch
  Delete "$INSTDIR\ThunderWatch.exe"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\ThunderWatch\ThunderWatch.lnk"
  Delete "$SMPROGRAMS\ThunderWatch\Uninstall.lnk"
  RMDir "$SMPROGRAMS\ThunderWatch"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "ThunderWatch"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ThunderWatch"
SectionEnd

Function KillRunningThunderWatch
  DetailPrint "Closing any running ThunderWatch processes before installing..."
  StrCpy $R0 0
  kill_loop_install:
    ExecWait '"$SYSDIR\taskkill.exe" /IM "ThunderWatch.exe" /F /T' $0
    Sleep 500
    nsExec::ExecToStack 'cmd /C tasklist /FI "IMAGENAME eq ThunderWatch.exe" /NH | findstr /I "ThunderWatch.exe"'
    Pop $1
    ${If} $1 == 0
      IntOp $R0 $R0 + 1
      ${If} $R0 >= 5
        MessageBox MB_ICONSTOP "ThunderWatch is still running (taskkill exit code $0). Please close it manually and run the installer again."
        Abort
      ${EndIf}
      Sleep 1000
      Goto kill_loop_install
    ${EndIf}
FunctionEnd

Function un.KillRunningThunderWatch
  DetailPrint "Closing any running ThunderWatch processes before uninstalling..."
  StrCpy $R0 0
  kill_loop_uninstall:
    ExecWait '"$SYSDIR\taskkill.exe" /IM "ThunderWatch.exe" /F /T' $0
    Sleep 500
    nsExec::ExecToStack 'cmd /C tasklist /FI "IMAGENAME eq ThunderWatch.exe" /NH | findstr /I "ThunderWatch.exe"'
    Pop $1
    ${If} $1 == 0
      IntOp $R0 $R0 + 1
      ${If} $R0 >= 5
        MessageBox MB_ICONSTOP "ThunderWatch is still running (taskkill exit code $0). Please close it manually and run the uninstaller again."
        Abort
      ${EndIf}
      Sleep 1000
      Goto kill_loop_uninstall
    ${EndIf}
FunctionEnd

Function .onInstFailed
  ${If} $R7 != "1"
    Return
  ${EndIf}
  ; Restore backed-up files, removing partially installed replacements first.
  ; If this was a fresh install, this also removes its partial app files.
  !insertmacro RestoreOwnedFile "ThunderWatch.exe" $R4 EXE
  !insertmacro RestoreOwnedFile "LICENSE" $R5 LICENSE
  !insertmacro RestoreOwnedFile "Uninstall.exe" $R6 UNINSTALL
  RMDir "$INSTDIR\.thunderwatch-upgrade-rollback"
FunctionEnd
