Unicode true
ManifestDPIAware true
!include "MUI2.nsh"

!define APPNAME "Kotha"
!define VERSION "2.4.0"
!ifdef FULL
  !define SRC "dist_full\Kotha"
  !define OUT "output\Kotha-Windows-Offline-Setup.exe"
  !define EDITION "Kotha Offline"
!else
  !define SRC "dist_lite\Kotha"
  !define OUT "output\Kotha-Windows-Setup.exe"
  !define EDITION "Kotha"
!endif
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Kotha"
!define OLDINNO "Software\Microsoft\Windows\CurrentVersion\Uninstall\{B6E2F3A1-6C1D-4E9B-9A57-4B4F1C0D2A11}_is1"

Name "${APPNAME}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\Kotha"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${EDITION} ${VERSION}"
VIProductVersion "2.4.0.0"
VIAddVersionKey "ProductName" "Kotha"
VIAddVersionKey "FileDescription" "Kotha voice typing installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Kotha"

!define MUI_ICON "assets\kotha.ico"
!define MUI_UNICON "assets\kotha.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\Kotha.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Kotha চালু করো"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function KillKotha
  nsExec::Exec 'taskkill /F /IM Kotha.exe'
  Sleep 800
FunctionEnd

Function un.KillKotha
  nsExec::Exec 'taskkill /F /IM Kotha.exe'
  Sleep 800
FunctionEnd

Function RemoveOldVersion
  ; remove v1.0 installed with the old Inno Setup installer (per-user or all-users)
  ReadRegStr $0 HKCU "${OLDINNO}" "UninstallString"
  StrCmp $0 "" 0 +2
  ReadRegStr $0 HKLM "${OLDINNO}" "UninstallString"
  StrCmp $0 "" done
  DetailPrint "Removing old Kotha version..."
  ExecWait '$0 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
  Sleep 1500
  done:
FunctionEnd

Section "Install"
  Call KillKotha
  Call RemoveOldVersion
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\Kotha"
  CreateShortcut "$SMPROGRAMS\Kotha\Kotha.lnk" "$INSTDIR\Kotha.exe"
  CreateShortcut "$SMPROGRAMS\Kotha\Uninstall Kotha.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\Kotha.lnk" "$INSTDIR\Kotha.exe"

  WriteRegStr HKCU "${UNKEY}" "DisplayName" "Kotha (কথা)"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "Publisher" "Manik Hossain"
  WriteRegStr HKCU "${UNKEY}" "URLInfoAbout" "https://www.manikhossain.in/"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\Kotha.exe"
  WriteRegStr HKCU "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Call un.KillKotha
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Kotha"
  Delete "$DESKTOP\Kotha.lnk"
  RMDir /r "$SMPROGRAMS\Kotha"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNKEY}"
SectionEnd
