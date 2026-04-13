# Camoufox v149 Fixup Patches — Generation Report

Generated: 2026-04-13
Source recovery doc: `docs/v149_manual_fixes_recovered.md`
Target source tree: `/app/camoufox-149.0-beta.1/` (inside `cfox` container)
Delivered patches: `patches/z-v149-fixup-*.patch`

## Ordering note — `z-` prefix

`scripts/patch.py` applies patches in alphabetical order (sorted by basename via `list_patches()` in `scripts/_mixin.py`). Several of the fixups depend on upstream patches that start with letters **after** `v` — specifically `voice-spoofing.patch`, `webgl-spoofing.patch`, `webrtc-ip-spoofing.patch`, `windows-theming-bug-modified.patch`. If the fixups were named `v149-fixup-*.patch` (without the `z-` prefix) they would run **before** those patches and their anchors would not match. Every fixup filename is therefore prefixed with `z-` so they sort after the entire existing patch set. The sort order that `patch.py` uses is: `… voice-spoofing.patch → webgl-spoofing.patch → webrtc-ip-spoofing.patch → windows-theming-bug-modified.patch → z-v149-fixup-01-* → … → z-v149-fixup-13-*`.

## Methodology

1. Started from a clean FF149 source tree (`git checkout -- . && git clean -fd`).
2. Applied all existing `patches/*.patch` files (in sorted order) with `patch -p1 -l --binary --forward`, letting broken patches produce `.rej` files.
3. Cleaned up all `.rej` / `.orig` files to establish a "post-existing-patches" baseline — this is the state the Camoufox build leaves the tree in just before our fixup patches run.
4. Generated each fixup patch by editing the baseline file(s) via a Python heredoc, running `diff -u` against a backup, and re-labeling the diff headers as `a/<path>` / `b/<path>` for `patch -p1` compatibility.
5. Tested each patch with `patch -p1 --dry-run -l --binary`.
6. Ran an end-to-end simulation: reset, apply all existing patches, apply all 11 fixups in order. All 11 applied successfully.

## Patches Created — summary

| # | File | Dry-run | Notes |
|---|------|---------|-------|
| 01 | `z-v149-fixup-01-per-context-api-decls.patch` | OK | A1 + A8 |
| 02 | `z-v149-fixup-02-timezone-api.patch` | OK | Covers #2, #6, #11, A7, A14 |
| 03 | `z-v149-fixup-03-layout-css-screen.patch` | OK | Covers #3, #4, #12 |
| 04 | `z-v149-fixup-04-geolocation.patch` | OK | Covers #5 |
| 05 | *(not created)* | N/A | Handled by existing `voice-spoofing.patch` (fixed on disk) |
| 06 | `z-v149-fixup-06-h2-fingerprint.patch` | OK | Covers #8, #9, #10 |
| 07 | `z-v149-fixup-07-webgl-spoofing-hunks7-8.patch` | OK | Covers A17 (reject hunks) |
| 08 | *(not created)* | N/A | A9 dedupe not needed — host `webgl-spoofing.patch` has single occurrences |
| 09 | `z-v149-fixup-09-workers-offscreen.patch` | OK | Covers A10, A11 |
| 10 | `z-v149-fixup-10-font-user-context.patch` | OK | Covers A12, A13 |
| 11 | `z-v149-fixup-11-moz-build-placement.patch` | OK | Covers #14 |
| 12 | `z-v149-fixup-12-mask-config-check-bool.patch` | OK | Covers A15 |
| 13 | `z-v149-fixup-13-multi-instance.patch` | OK | Covers A19 |

All 11 created patches applied cleanly both in `--dry-run` and in the full sequential apply test.

## Collateral change

* `patches/voice-spoofing.patch` on the host was malformed (the first hunk header `@@ -63,4 +63,7 @@` had incorrect line counts, so `patch -p1` aborted with "malformed patch at line 11"). I replaced it with the container's working copy (`/app/patches/voice-spoofing.patch`, identical content but correctly formatted). This is the "rewritten" voice-spoofing patch from section 0.4 of the recovery doc and is necessary for fixup 12 (which assumes `MaskConfig::GetBool` has already been introduced in `nsSynthVoiceRegistry.cpp`).

## Per-patch details

### 01 — `z-v149-fixup-01-per-context-api-decls.patch`

**Target:** `dom/base/nsGlobalWindowInner.h`

**What it does:**
* Adds `SetAudioFingerprintSeed`, `SetScreenDimensions`, `SetScreenColorDepth` method declarations immediately after `GetPaintWorklet(ErrorResult&)` (recovery doc section #1).
* Moves `SetWebGLVendor` / `SetWebGLRenderer` out of the `protected:` block and into the `public:` block so the WebIDL bindings can call them (recovery doc A8).

**Dry-run:** passes cleanly.

### 02 — `z-v149-fixup-02-timezone-api.patch`

**Targets:** `js/public/Date.h`, `js/src/vm/DateTime.cpp`, `dom/base/Navigator.cpp`

**What it does:**
* `js/public/Date.h`: adds `extern JS_PUBLIC_API bool SetTimeZoneOverride(const char*)` and `extern JS_PUBLIC_API bool SetRealmTimeZoneOverride(JSContext*, const char*)` declarations (recovery doc #2).
* `js/src/vm/DateTime.cpp`:
  * Replaces `mozilla::intl::TimeZone::IsValidTimeZoneId(timeZoneId)` check in `JS::SetRealmTimeZoneOverride` with a `!*timeZoneId` null-terminator check (IsValidTimeZoneId was removed in FF149) — recovery #6.1.
  * Switches `timeZoneOverride_ = std::string(...)` in the `DateTimeInfo(const char*)` constructor to allocate a `RefPtr<JS::TimeZoneString>(new JS::TimeZoneString(...))` — recovery #6.2 (with the A7.1 corrected form).
  * Adds the full `JS::SetTimeZoneOverride` function body (calls `_putenv_s("TZ", ...)` + `js::ResetTimeZoneInternal(...)`) immediately before `JS::SetRealmTimeZoneOverride` — recovery A7. This is critical; the declaration without a body produces unresolved externals.
* `dom/base/Navigator.cpp`:
  * Replaces `nsJSUtils::SetTimeZoneOverride(tz.value().c_str())` (removed in FF149) with `_putenv_s("TZ", tz.value().c_str()); JS::ResetTimeZone();` — recovery #11.1/#11.2.
  * Adds `#include "js/Date.h"` just after `#include "MaskConfig.hpp"` — recovery #11.3/A14.

**Dry-run:** passes cleanly.

### 03 — `z-v149-fixup-03-layout-css-screen.patch`

**Targets:** `layout/style/nsMediaFeatures.cpp`, `layout/mathml/nsMathMLChar.cpp`, `gfx/thebes/gfxPlatformFontList.cpp`

**What it does:**
* `nsMediaFeatures.cpp`: replaces the RDM-pane `GetRDMDeviceSize` / BrowsingContext `GetScreenAreaOverride` lookup with a Camoufox `ScreenDimensionManager::GetDimensions(userContextId, &w, &h)` lookup — recovery #3. Existing `#include` entries for `ScreenDimensionManager.h` and `nsGlobalWindowInner.h` are already in place from other patches, so no extra includes are needed.
* `nsMathMLChar.cpp`: appends the new trailing `uint32_t aPrivateBrowsingId = 0` argument to the font-fallback call at the anchor `nsTextFrameUtils::Flags(), nullptr);` — recovery #4. Adds a `// TODO: Extract private browsing ID` comment.
* `gfxPlatformFontList.cpp`: adds `mozilla::dom::Document* GetDocument() const override { return nullptr; }` to the inner `ListFontsVisibilityProvider` class (FF149's `FontVisibilityProvider` has `GetDocument` as pure-virtual) — recovery #12.

**Dry-run:** passes cleanly.

### 04 — `z-v149-fixup-04-geolocation.patch`

**Target:** `dom/system/NetworkGeolocationProvider.sys.mjs`

**What it does:** replaces the raw `fetchLocation` path with a Camoufox `CAMOU_CONFIG` override path — if `geolocation:latitude` and `geolocation:longitude` are set in MaskConfig, uses those values directly (with accuracy estimated from decimal precision if unset) instead of calling the remote network geo service. Recovery doc #5.

**Dry-run:** passes cleanly.

### 05 — NOT created

The existing `patches/voice-spoofing.patch` (after the host copy was replaced with the known-good container copy) already applies all four edits described in recovery sections 7.1 and 7.2 (moz.build `LOCAL_INCLUDES += ['/camoucfg']`, `#include "MaskConfig.hpp"`, MVoices loader, blockIfNotDefined early return, fake voice block). No fixup patch is required.

### 06 — `z-v149-fixup-06-h2-fingerprint.patch`

**Targets:** `netwerk/protocol/http/Http2Session.h`, `Http2Session.cpp`, `Http2Compression.cpp`

**What it does:**
* `Http2Session.h`: replaces the `// 6 is SETTINGS_TYPE_MAX_HEADER_LIST - advisory, we ignore it` comment with an actual `SETTINGS_TYPE_MAX_HEADER_LIST_SIZE = 6,` enum entry (recovery #8).
* `Http2Session.cpp`: all 9 substitutions from recovery #9 (MaskConfig include, `maxSettings 6 -> 8`, and six configurable SETTINGS entries for HEADER_TABLE_SIZE / ENABLE_PUSH / MAX_CONCURRENT / INITIAL_WINDOW / MAX_HEADER_LIST_SIZE / WINDOW_UPDATE) plus the disablePriority `bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority").value_or(false);` fix from #9.10. Used `.value_or(false)` since the existing MaskConfig in `camoucfg/MaskConfig.hpp` already provides `CheckBool`, but `.value_or` keeps parity with the original Mar 27 recipe and avoids churn in this file.
* `Http2Compression.cpp`: adds `#include "MaskConfig.hpp"` after `#include "Http2HuffmanOutgoing.h"`, and replaces the fixed `ProcessHeader(":method"/":path"/":authority"/":scheme")` block inside `!simpleConnectForm` with a loop over a `MaskConfig::GetString("h2:pseudoHeaderOrder")` string (default `"mpas"`). Used loop variable name `ch` (not `c`) to avoid shadowing the outer `c` local. Recovery #10.

**Dry-run:** passes cleanly (8 hunks to Http2Session.cpp, 1 hunk to Http2Session.h, 2 hunks to Http2Compression.cpp).

### 07 — `z-v149-fixup-07-webgl-spoofing-hunks7-8.patch`

**Target:** `dom/canvas/ClientWebGLContext.cpp`

**What it does:** adds the `WebGLParamsManager::GetRenderer(GetUserContextId(), stored)` + `MaskConfig::GetString("webGl:renderer")` lookup block (with `break` fast-return) before the `ShouldResistFingerprinting(RFPTarget::WebGLRenderInfo) ||` check in `UNMASKED_RENDERER_WEBGL`, and the analogous vendor block before the `ShouldResistFingerprinting(...)` check in `UNMASKED_VENDOR_WEBGL`. Recovery doc A17 — exact verbatim `diff` text preserved at the end of the doc.

Uses the regex substitution form from A17's fix-up script so that the FF149-specific context (new `WebGLVendorRandomize` / `WebGLVendorConstant` / `Base64Encode` surrounding code) is not touched.

**Dry-run:** passes cleanly (both hunks apply; offset varies depending on whether the webgl-spoofing patch above it fully applied — `patch -p1` handles the offset automatically).

### 08 — NOT created

Recovery doc A9 describes deduplicating triplicated `WebGLParamsManager` / `setWebGLVendor` / `setWebGLRenderer` blocks in several files. A direct inspection of the current `patches/webgl-spoofing.patch` shows each block only once (not three times), and the current FF149 source tree (after applying the existing `webgl-spoofing.patch`) has exactly one occurrence in each target file (`dom/base/nsGlobalWindowInner.h`, `dom/base/nsGlobalWindowInner.cpp`, `dom/webidl/Window.webidl`, `dom/base/moz.build`). A9 described a Mar 29 runtime issue that was already resolved when the patch was edited on disk; no fixup is needed.

### 09 — `z-v149-fixup-09-workers-offscreen.patch`

**Targets:** `dom/workers/WorkerPrivate.h`, `dom/canvas/OffscreenCanvas.h`

**What it does:**
* `WorkerPrivate.h`: adds `Document* GetDocument() const;` right after `nsPIDOMWindowInner* GetAncestorWindow() const;` — recovery A10.
* `OffscreenCanvas.h`: adds `mozilla::dom::Document* GetDocument() const;` right after the `FONT_VISIBILITY_PROVIDER_IMPL` macro — recovery A11. Note the correct path is `dom/canvas/OffscreenCanvas.h` (the Mar 27 doc had a typo saying `dom/html/`).

**Dry-run:** passes cleanly.

### 10 — `z-v149-fixup-10-font-user-context.patch`

**Targets:** `gfx/thebes/gfxTextRun.h`, `gfx/thebes/gfxTextRun.cpp`

**What it does:** 10 coordinated edits to `gfxTextRun.h` that plumb a `uint32_t aUserContextId = 0` parameter through:
1. `gfxTextRun::GetUserContextId()` override returning `mUserContextId` (new public getter).
2. `gfxTextRun::Create()` static factory gets trailing `uint32_t aUserContextId = 0`.
3. `gfxTextRun::gfxTextRun()` constructor gets the same trailing param.
4. New `uint32_t mUserContextId;` member immediately before `bool mDontSkipDrawing`.
5. `gfxFontGroup::MakeTextRun` (Parameters* template) gets trailing `uint32_t aUserContextId = 0`.
6. `gfxFontGroup::MakeTextRun` (DrawTarget* template) gets the same param AND forwards it in the inline body.
7. `gfxFontGroup::MakeEmptyTextRun` gets the trailing param.
8. `gfxFontGroup::MakeSpaceTextRun` gets the trailing param.
9. `gfxFontGroup::MakeBlankTextRun` template gets the trailing param.
10. New `uint32_t mUserContextId = 0;` member in `gfxFontGroup` immediately after `mFontListGeneration`.

Plus one edit to `gfxTextRun.cpp`:
* `gfxFontGroup::MakeBlankTextRun` definition gets the new `uint32_t aUserContextId` parameter in the signature (recovery A13).

**Edits skipped:** the duplicate `mUserContextId` insert that A12.10 says was accidentally added twice in event #734 — only the single, correct insert after `mFontListGeneration` is present.

**Dry-run:** passes cleanly (6 hunks to .h, 1 hunk to .cpp).

### 11 — `z-v149-fixup-11-moz-build-placement.patch`

**Target:** `dom/base/moz.build`

**What it does:** inserts `"NavigatorManager.cpp",` in the `UNIFIED_SOURCES` list immediately after `"Navigator.cpp",`. Recovery #14. Does NOT touch the `EXPORTS` block where `NavigatorManager.h` already lives.

**Dry-run:** passes cleanly (Hunk applies with offset -17 lines because the position is shifted by earlier patches in the apply chain — this is normal).

### 12 — `z-v149-fixup-12-mask-config-check-bool.patch`

**Targets:** `layout/style/GlobalStyleSheetCache.cpp`, `dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp`, `dom/media/MediaDevices.cpp`, `docshell/base/BrowsingContext.cpp`

**What it does:** renames `MaskConfig::GetBool(` → `MaskConfig::CheckBool(` in all four files. `GetBool` returns `std::optional<bool>` (which is always truthy in an `if`), while `CheckBool` returns a plain `bool`. Recovery A15. The `CheckBool` method is confirmed to exist in `additions/camoucfg/MaskConfig.hpp` (as a thin wrapper around `GetBool(key).value_or(false)`).

**Note:** `Http2Session.cpp`'s `disablePriority` pattern is NOT renamed here — it's already handled inside patch 06 by the `.value_or(false)` form. No functional difference; both paths produce `bool`.

**Dry-run:** passes cleanly against the post-patched baseline (requires the existing `voice-spoofing.patch`, `global-style-sheets.patch`, `media-device-spoofing.patch`, `disable-remote-subframes.patch` to have applied first to introduce the `MaskConfig::GetBool(...)` call sites — these patches do apply cleanly on their own).

### 13 — `z-v149-fixup-13-multi-instance.patch`

**Target:** `toolkit/components/remote/nsRemoteService.cpp`

**What it does:** inserts `return NS_ERROR_NOT_AVAILABLE;  // Camoufox: multi-instance` as the first statement of `nsRemoteService::SendCommandLine(const nsACString&, size_t, const char**, bool)`, and inserts `return;  // Camoufox: multi-instance` as the first statement of `nsRemoteService::StartupServer()`. The rest of the original bodies remain unreachable but are preserved. Recovery A19.

**Path note:** the recovery doc (section A19) originally referred to `toolkit/xre/nsRemoteService.cpp` — the actual FF149 path is `toolkit/components/remote/nsRemoteService.cpp`. Both Mar 27 and Mar 29 recipes used a Python substitution that targets the function names, not the path, so the tool was insensitive to the path confusion.

**Dry-run:** passes cleanly.

## Fixes skipped per instructions

As instructed, I did not create patches or modify existing patches for:
* `settings/camoufox.cfg` changes (A4, A5, A6) — not source code.
* `patches/chromeutil.patch` DEBUG printf removal (A1).
* `patches/librewolf/devtools-bypass.patch` rebrand (A2).
* `patches/anti-font-fingerprinting.patch` printf removal (A3).
* `pythonlib/camoufox/extension/experiment/api.js` WheelEvent fix (#16).
* `additions/browser/branding/camoufox/configure.sh` (#13).
* `third_party/rust/*/Cargo.toml.orig` bulk restore (A18).
* `js/src/vm/Realm.cpp` (A16) — confirmed NO FIX NEEDED, FF149 has the native implementation. The `timezone-spoofing.patch` hunk that targets `Realm.cpp` at line 545 will always reject; per A16, the resulting `.rej` file can be safely deleted by the build process.

## Existing patch rejects that remain after fixups

After applying all existing patches + all 11 fixups in sequence, the following hunks in existing patches still reject (none are in scope for the recovery doc):

| Existing patch | File | Hunk | Reason |
|---|---|---|---|
| `audio-fingerprint-manager.patch` | `dom/base/nsGlobalWindowInner.h` | #1 @ 676 | Anchor near `SetFontSpacingSeed` drifted; tries to add decl already added by our fixup 01 in a different location. Out of scope. |
| `navigator-spoofing.patch` | `dom/base/nsGlobalWindowInner.h` | #1 @ 700 | Tries to add `SetNavigatorPlatform`/`Oscpu`/`HardwareConcurrency`/`UserAgent` decls before a `SetCanvasSeed` anchor that no longer exists. Not in recovery doc. |
| `navigator-spoofing.patch` | `dom/base/nsGlobalWindowInner.cpp` | #1 | Include anchor drift. Not in recovery doc. |
| `navigator-spoofing.patch` | `dom/base/moz.build` | #2 | Adds `NavigatorManager.cpp` to a different list block. Our fixup 11 adds it to `UNIFIED_SOURCES` which is the correct placement; the rejected hunk is redundant. |
| `network-patches.patch` | `netwerk/protocol/http/nsHttpHandler.cpp` | #4 @ 2112 | Not in recovery doc. |
| `screen-spoofing.patch` | `dom/base/nsGlobalWindowInner.h` | #1 @ 750 | Our fixup 01 already adds `SetScreenDimensions`/`SetScreenColorDepth` decls. The rejected hunk is redundant. |
| `timezone-spoofing.patch` | `js/src/vm/Realm.cpp` | #1 @ 545 | Per recovery A16, FF149 already ships the native implementation. Safely delete the `.rej` file. |
| `windows-theming-bug-modified.patch` | `browser/app/Makefile.in` | #1 @ 23 | Not in recovery doc. |

**Recommended handling:** the Camoufox build script should delete `.rej` files for `audio-fingerprint-manager.patch`, `navigator-spoofing.patch`, `screen-spoofing.patch`, `timezone-spoofing.patch` after the fixup patches run, or (better) these upstream patches should themselves be rewritten for FF149 by a follow-up session. The fixups in this session address only the specific items in the recovery doc.

## End-to-end test result

```
--- Applying existing patches ---
(11 FAILED: anti-font-fingerprinting, audio-fingerprint-manager, chromeutil,
 geolocation-spoofing, h2-fingerprint-spoofing, navigator-spoofing,
 network-patches, screen-spoofing, timezone-spoofing, webgl-spoofing,
 windows-theming-bug-modified — all for reasons outside this task's scope)
--- Dry-run fixup patches ---
OK: z-v149-fixup-01-per-context-api-decls.patch
OK: z-v149-fixup-02-timezone-api.patch
OK: z-v149-fixup-03-layout-css-screen.patch
OK: z-v149-fixup-04-geolocation.patch
OK: z-v149-fixup-06-h2-fingerprint.patch
OK: z-v149-fixup-07-webgl-spoofing-hunks7-8.patch
OK: z-v149-fixup-09-workers-offscreen.patch
OK: z-v149-fixup-10-font-user-context.patch
OK: z-v149-fixup-11-moz-build-placement.patch
OK: z-v149-fixup-12-mask-config-check-bool.patch
OK: z-v149-fixup-13-multi-instance.patch
```

```
--- Apply fixup patches (not dry-run) ---
APPLIED: z-v149-fixup-01-per-context-api-decls.patch
APPLIED: z-v149-fixup-02-timezone-api.patch
APPLIED: z-v149-fixup-03-layout-css-screen.patch
APPLIED: z-v149-fixup-04-geolocation.patch
APPLIED: z-v149-fixup-06-h2-fingerprint.patch
APPLIED: z-v149-fixup-07-webgl-spoofing-hunks7-8.patch
APPLIED: z-v149-fixup-09-workers-offscreen.patch
APPLIED: z-v149-fixup-10-font-user-context.patch
APPLIED: z-v149-fixup-11-moz-build-placement.patch
APPLIED: z-v149-fixup-12-mask-config-check-bool.patch
APPLIED: z-v149-fixup-13-multi-instance.patch
```

All 11 fixup patches apply cleanly both in dry-run and in the full sequential apply test.
