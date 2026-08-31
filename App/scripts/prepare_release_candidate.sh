#!/usr/bin/env bash
# Prepare a local Neckline release candidate. This is the single version/build
# entry point: it updates the client source of truth, server health version,
# regenerates the Xcode project, and verifies every generated representation.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <marketing-version> <build-number>" >&2
  exit 64
fi

RELEASE_VERSION="$1"
RELEASE_BUILD="$2"
if [[ ! "$RELEASE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ ! "$RELEASE_BUILD" =~ ^[1-9][0-9]*$ ]]; then
  echo "version must be X.Y.Z and build must be a positive integer" >&2
  exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$APP_DIR/.." && pwd)"
PROJECT_YML="$APP_DIR/project.yml"
PBXPROJ="$APP_DIR/Neckline.xcodeproj/project.pbxproj"
BACKEND_APP="$REPO_ROOT/Backend/neckline/api/app.py"
BACKEND_PYPROJECT="$REPO_ROOT/Backend/pyproject.toml"
ICON_NAME="AppIconV${RELEASE_VERSION//./}B${RELEASE_BUILD}"
export RELEASE_VERSION RELEASE_BUILD ICON_NAME

perl -0pi -e 's/^(\s*MARKETING_VERSION:\s*)"[0-9]+\.[0-9]+\.[0-9]+"/${1}"$ENV{RELEASE_VERSION}"/mg' "$PROJECT_YML"
perl -0pi -e 's/^(\s*CURRENT_PROJECT_VERSION:\s*)"?[0-9]+"?/${1}"$ENV{RELEASE_BUILD}"/mg' "$PROJECT_YML"
perl -0pi -e 's/^VERSION = "v[0-9]+\.[0-9]+\.[0-9]+"/VERSION = "v$ENV{RELEASE_VERSION}"/m' "$BACKEND_APP"
perl -0pi -e 's/^RELEASE_SET = "v[0-9]+\.[0-9]+\.[0-9]+-b[1-9][0-9]*"/RELEASE_SET = "v$ENV{RELEASE_VERSION}-b$ENV{RELEASE_BUILD}"/m' "$BACKEND_APP"
perl -0pi -e 's/^(version = ")[0-9]+\.[0-9]+\.[0-9]+"/${1}$ENV{RELEASE_VERSION}"/m' "$BACKEND_PYPROJECT"
perl -0pi -e 's/^(\s*ASSETCATALOG_COMPILER_APPICON_NAME:\s*)\S+/${1}$ENV{ICON_NAME}/m' "$PROJECT_YML"

cd "$APP_DIR"
xcodegen generate

[[ "$(rg -c "MARKETING_VERSION: \"$RELEASE_VERSION\"" "$PROJECT_YML")" == "2" ]]
[[ "$(rg -c "CURRENT_PROJECT_VERSION: \"$RELEASE_BUILD\"" "$PROJECT_YML")" == "1" ]]
rg -qx "VERSION = \"v$RELEASE_VERSION\"" "$BACKEND_APP"
rg -qx "RELEASE_SET = \"v$RELEASE_VERSION-b$RELEASE_BUILD\"" "$BACKEND_APP"
rg -qx "version = \"$RELEASE_VERSION\"" "$BACKEND_PYPROJECT"
rg -qx "\s*ASSETCATALOG_COMPILER_APPICON_NAME: $ICON_NAME" "$PROJECT_YML"
test -d "$APP_DIR/Neckline/Resources/Assets.xcassets/$ICON_NAME.appiconset"
[[ "$(rg -c "MARKETING_VERSION = $RELEASE_VERSION;" "$PBXPROJ")" == "4" ]]
[[ "$(rg -c "CURRENT_PROJECT_VERSION = $RELEASE_BUILD;" "$PBXPROJ")" == "2" ]]

echo "Release candidate metadata ready: v$RELEASE_VERSION-b$RELEASE_BUILD"
