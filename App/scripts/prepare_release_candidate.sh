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
export RELEASE_VERSION RELEASE_BUILD

perl -0pi -e 's/^(\s*MARKETING_VERSION:\s*)"[0-9]+\.[0-9]+\.[0-9]+"/${1}"$ENV{RELEASE_VERSION}"/mg' "$PROJECT_YML"
perl -0pi -e 's/^(\s*CURRENT_PROJECT_VERSION:\s*)"?[0-9]+"?/${1}"$ENV{RELEASE_BUILD}"/mg' "$PROJECT_YML"
perl -0pi -e 's/^VERSION = "v[0-9]+\.[0-9]+\.[0-9]+"/VERSION = "v$ENV{RELEASE_VERSION}"/m' "$BACKEND_APP"

cd "$APP_DIR"
xcodegen generate

[[ "$(rg -c "MARKETING_VERSION: \"$RELEASE_VERSION\"" "$PROJECT_YML")" == "2" ]]
[[ "$(rg -c "CURRENT_PROJECT_VERSION: \"$RELEASE_BUILD\"" "$PROJECT_YML")" == "1" ]]
rg -qx "VERSION = \"v$RELEASE_VERSION\"" "$BACKEND_APP"
[[ "$(rg -c "MARKETING_VERSION = $RELEASE_VERSION;" "$PBXPROJ")" == "4" ]]
[[ "$(rg -c "CURRENT_PROJECT_VERSION = $RELEASE_BUILD;" "$PBXPROJ")" == "2" ]]

echo "Release candidate metadata ready: v$RELEASE_VERSION Build $RELEASE_BUILD"
