#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--replace] [--no-desktop-link] /absolute/path/to/N.O.O.B.app" >&2
}

replace=false
desktop_link=true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --replace) replace=true; shift ;;
    --no-desktop-link) desktop_link=false; shift ;;
    --help|-h) usage; exit 0 ;;
    --*) usage; exit 2 ;;
    *) break ;;
  esac
done

[[ $# -eq 1 ]] || { usage; exit 2; }
[[ $(uname -s) == "Darwin" ]] || { echo "This installer is for macOS." >&2; exit 3; }
SOURCE_APP=$1
[[ "$SOURCE_APP" = /* ]] || { echo "Pass an absolute .app path." >&2; exit 4; }
[[ -d "$SOURCE_APP/Contents" ]] || { echo "Invalid app bundle: $SOURCE_APP" >&2; exit 5; }

PLIST="$SOURCE_APP/Contents/Info.plist"
bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST" 2>/dev/null || true)
[[ "$bundle_id" == "com.neveroutofbounds.noob-operator" ]] || {
  echo "Refusing unexpected bundle identifier: ${bundle_id:-missing}" >&2
  exit 6
}

INSTALL_ROOT="$HOME/Applications"
TARGET_APP="$INSTALL_ROOT/N.O.O.B.app"
DESKTOP_APP="$HOME/Desktop/N.O.O.B.app"
mkdir -p "$INSTALL_ROOT"

if [[ -e "$TARGET_APP" && "$replace" != true ]]; then
  echo "$TARGET_APP already exists; use --replace after closing the app." >&2
  exit 7
fi

STAGING_APP="$INSTALL_ROOT/.N.O.O.B.app.install-$$"
[[ ! -e "$STAGING_APP" ]] || { echo "Unexpected staging collision." >&2; exit 8; }
trap 'rm -rf "$STAGING_APP"' EXIT
/usr/bin/ditto "$SOURCE_APP" "$STAGING_APP"
staged_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$STAGING_APP/Contents/Info.plist")
[[ "$staged_id" == "$bundle_id" ]] || { echo "Staged bundle validation failed." >&2; exit 9; }

if [[ -e "$TARGET_APP" ]]; then
  BACKUP_APP="$INSTALL_ROOT/N.O.O.B.backup-$(date +%Y%m%d-%H%M%S).app"
  mv "$TARGET_APP" "$BACKUP_APP"
  echo "Previous app preserved at $BACKUP_APP"
fi
mv "$STAGING_APP" "$TARGET_APP"
trap - EXIT

if [[ "$desktop_link" == true ]]; then
  mkdir -p "$HOME/Desktop"
  if [[ -L "$DESKTOP_APP" ]]; then
    current_target=$(readlink "$DESKTOP_APP" || true)
    [[ "$current_target" == "$TARGET_APP" ]] || {
      echo "Refusing to replace unrelated Desktop link: $DESKTOP_APP" >&2
      exit 10
    }
  elif [[ -e "$DESKTOP_APP" ]]; then
    echo "Installed app, but Desktop path already exists: $DESKTOP_APP" >&2
    exit 11
  else
    ln -s "$TARGET_APP" "$DESKTOP_APP"
  fi
fi

echo "Installed: $TARGET_APP"
[[ "$desktop_link" == true ]] && echo "Desktop launcher: $DESKTOP_APP"
echo "No bearer token, SSH private key, or known-device runtime state was copied from the build."
