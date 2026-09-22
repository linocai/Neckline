#!/usr/bin/env bash
# XcodeGen currently emits the application product reference with
# `lastKnownFileType`.  Keep the user's explicit product type after every
# deterministic generation; this only touches the one generated Neckline.app
# product reference and fails closed if the expected representation drifts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PBXPROJ="$SCRIPT_DIR/../Neckline.xcodeproj/project.pbxproj"

perl -0pi -e 's{(/\* Neckline\.app \*/ = \{isa = PBXFileReference; )(?:includeInIndex = 0; (?:lastKnownFileType|explicitFileType) = wrapper\.application;|explicitFileType = wrapper\.application; includeInIndex = 0;) path = Neckline\.app; sourceTree = BUILT_PRODUCTS_DIR; \};}{$1explicitFileType = wrapper.application; includeInIndex = 0; path = Neckline.app; sourceTree = BUILT_PRODUCTS_DIR; \};}' "$PBXPROJ"
rg -q '/\* Neckline\.app \*/ = \{isa = PBXFileReference; explicitFileType = wrapper\.application; includeInIndex = 0; path = Neckline\.app; sourceTree = BUILT_PRODUCTS_DIR; \};' "$PBXPROJ"
