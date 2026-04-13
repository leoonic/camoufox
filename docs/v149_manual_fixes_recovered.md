# Camoufox v149 — Manual Fixes Recovered from Transcript

Source: `C:\Users\leone\.claude\projects\C--Users-leone-OneDrive-Desktop-Programacion-Trabajo-Proyecto-Shopee\e19e7540-f80a-4bf2-97b9-f3fca4e93c57.jsonl`
(Session 2026-03-27, Camoufox v149 build from Firefox 149.0 source, container `cfox`, source tree
`/app/camoufox-149.0-beta.1`.)

This file is the complete recipe of every manual edit that was applied directly to the
Firefox 149.0 source tree to make the Camoufox build succeed. All fixes were executed via
`docker exec cfox` (or the `container-exec-mcp` MCP tool) using `sed -i` or inline Python
heredocs — **none of them were committed to git**, so this file is the only surviving copy.

Paths are relative to the FF149 source tree root (`/app/camoufox-149.0-beta.1/...`).

---

## 0. Pre-build — Patches that were disabled or rewritten

These are not strictly "manual source fixes" but are required context so the rest of the
recipe applies cleanly.

### 0.1 `patches/_disabled/` — moved out of the apply list

The following patches were moved to `patches/_disabled/` and must NOT be applied:

- `patches/playwright/0-playwright.patch`
- `patches/playwright/1-leak-fixes.patch`
- `patches/canvas-spoofing.patch` (already removed in an earlier session — detected by anti-bot per daijro)
- `patches/no-css-animations.patch` (already removed — breaks captcha CSS)

```bash
mkdir -p patches/_disabled && \
mv patches/playwright/0-playwright.patch patches/_disabled/ && \
mv patches/playwright/1-leak-fixes.patch patches/_disabled/
```

### 0.2 `upstream.sh` — FF version

```sh
version=149.0
release=beta.1
closedsrc_rev=1.0.0
```

### 0.3 `patches/librewolf/disable-data-reporting-at-compile-time.patch` — REWRITTEN

The librewolf patch was rewritten so that the UA says `Firefox/149.0` (via `MOZ_APP_UA_NAME`)
even though `MOZ_APP_NAME` stays `camoufox`. Full patch content:

```patch
diff --git a/browser/moz.configure b/browser/moz.configure
index e8b401a7df..7b63573c17 100644
--- a/browser/moz.configure
+++ b/browser/moz.configure
@@ -5,15 +5,17 @@
 # file, You can obtain one at http://mozilla.org/MPL/2.0/.

 imply_option("MOZ_PLACES", True)
-imply_option("MOZ_SERVICES_HEALTHREPORT", True)
+imply_option("MOZ_SERVICES_HEALTHREPORT", False)
 imply_option("MOZ_SERVICES_SYNC", True)
 imply_option("MOZ_DEDICATED_PROFILES", True)
 imply_option("MOZ_BLOCK_PROFILE_DOWNGRADE", True)
-imply_option("MOZ_NORMANDY", True)
+imply_option("MOZ_NORMANDY", False)
 imply_option("MOZ_PROFILE_MIGRATOR", True)


-imply_option("MOZ_APP_VENDOR", "Mozilla")
+imply_option("MOZ_APP_VENDOR", "Camoufox")
+imply_option("MOZ_APP_PROFILE", "camoufox")
+imply_option("MOZ_APP_UA_NAME", "Firefox")
 imply_option("MOZ_APP_ID", "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}")
 # Include the DevTools client, not just the server (which is the default)
 imply_option("MOZ_DEVTOOLS", "all")
diff --git a/python/mach/mach/telemetry.py b/python/mach/mach/telemetry.py
index bdc3b958b3..1d42c0b927 100644
--- a/python/mach/mach/telemetry.py
+++ b/python/mach/mach/telemetry.py
@@ -92,10 +92,7 @@


 def is_telemetry_enabled(settings):
-    if os.environ.get("DISABLE_TELEMETRY") == "1":
-        return False
-
-    return settings.mach_telemetry.is_enabled
+    return False


 def arcrc_path():
```

> **Reason**: FF149 honors `MOZ_APP_UA_NAME` for the UA string. Setting it to `Firefox`
> makes `navigator.userAgent` return `Firefox/149.0` instead of `Camoufox/149.0`, while
> `MOZ_APP_NAME=camoufox` still controls the binary name.

### 0.4 `patches/voice-spoofing.patch` — REWRITTEN

The voice-spoofing patch on disk had bad hunk offsets for FF149. It was rewritten so it
applies cleanly against `dom/media/webspeech/synth/moz.build` and
`dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp`. The final contents of both files
are documented below (sections 7.1 and 7.2) as manual edits because the rewritten patch was
still partly applied by Python heredoc rather than `patch -p1`.

---

## 1. `dom/base/nsGlobalWindowInner.h` — add per-context setter declarations

**Reason**: FF149 moved/renamed some method declarations in the class body, so the hunk in
`nsGlobalWindowInner.h` from the per-context patches failed. Re-added manually via `sed`.

**Anchor**: Line immediately after `mozilla::dom::Worklet* GetPaintWorklet(mozilla::ErrorResult& aRv);`

**Exact command executed**:

```bash
sed -i '/mozilla::dom::Worklet\* GetPaintWorklet(mozilla::ErrorResult& aRv);/{
a\
\
  // Audio fingerprint seed for per-context audio fingerprinting\
  void SetAudioFingerprintSeed(uint32_t seed, mozilla::ErrorResult\& aRv);\
\
  // Screen dimension methods for per-context screen spoofing\
  void SetScreenDimensions(int32_t aWidth, int32_t aHeight, mozilla::ErrorResult\& aRv);\
  void SetScreenColorDepth(int32_t aColorDepth, mozilla::ErrorResult\& aRv);
}' dom/base/nsGlobalWindowInner.h
```

**BEFORE** (at the anchor line):

```cpp
  mozilla::dom::Worklet* GetPaintWorklet(mozilla::ErrorResult& aRv);
```

**AFTER**:

```cpp
  mozilla::dom::Worklet* GetPaintWorklet(mozilla::ErrorResult& aRv);

  // Audio fingerprint seed for per-context audio fingerprinting
  void SetAudioFingerprintSeed(uint32_t seed, mozilla::ErrorResult& aRv);

  // Screen dimension methods for per-context screen spoofing
  void SetScreenDimensions(int32_t aWidth, int32_t aHeight, mozilla::ErrorResult& aRv);
  void SetScreenColorDepth(int32_t aColorDepth, mozilla::ErrorResult& aRv);
```

> **Note**: The session notes also mention `SetWebGLVendor`/`SetWebGLRenderer` being moved
> from `protected` to `public` in older FF146 work, but that specific edit does NOT appear in
> this JSONL — it may have been done in a prior session. If the current source tree already
> has them declared at all (even private), leave them as-is; if they're missing, add them in
> the same `public` block with `void SetWebGLVendor(const nsAString& aVendor, ErrorResult& aRv);`
> and `void SetWebGLRenderer(const nsAString& aRenderer, ErrorResult& aRv);`.

---

## 2. `js/public/Date.h` — declare timezone-override public API

**Reason**: FF149 does not ship these declarations by default; the per-context timezone
code needs them visible to `dom/base/Navigator.cpp` and `js/src/vm/DateTime.cpp`.

**Anchor**: Line after `extern JS_PUBLIC_API void ResetTimeZone();`

**Exact command**:

```bash
sed -i '/^extern JS_PUBLIC_API void ResetTimeZone();$/a\
\
extern JS_PUBLIC_API bool SetTimeZoneOverride(const char* timezoneId);\
// Camoufox: per-context timezone override\
extern JS_PUBLIC_API bool SetRealmTimeZoneOverride(JSContext* cx, const char* timezoneId);' js/public/Date.h
```

**BEFORE**:

```cpp
extern JS_PUBLIC_API void ResetTimeZone();
```

**AFTER**:

```cpp
extern JS_PUBLIC_API void ResetTimeZone();

extern JS_PUBLIC_API bool SetTimeZoneOverride(const char* timezoneId);
// Camoufox: per-context timezone override
extern JS_PUBLIC_API bool SetRealmTimeZoneOverride(JSContext* cx, const char* timezoneId);
```

---

## 3. `layout/style/nsMediaFeatures.cpp` — per-context screen dimensions for CSS MQ

**Reason**: The per-context patch's hunk rejected in FF149 because the upstream block that
it was anchored on was rewritten. We replace the RDM/WebDriver screen-size lookup with a
`ScreenDimensionManager` lookup keyed on the window's user-context ID.

**BEFORE** (Python heredoc `old_block`):

```cpp
  // Media queries in documents in an RDM pane should use the simulated
  // device size.
  Maybe<CSSIntSize> deviceSize =
      nsGlobalWindowOuter::GetRDMDeviceSize(aDocument);
  if (deviceSize.isSome()) {
    return CSSPixel::ToAppUnits(deviceSize.value());
  }

  // Media queries in documents should use an override set with WebDriver BiDi
  // if it exists.
  if (dom::BrowsingContext* bc = aDocument.GetBrowsingContext()) {
    Maybe<CSSIntSize> screenSize = bc->GetScreenAreaOverride();
    if (screenSize.isSome()) {
      return CSSPixel::ToAppUnits(screenSize.value());
    }
  }
```

**AFTER** (`new_block`):

```cpp
  // Camoufox: per-context screen dimensions for CSS device-width/height queries
  if (nsPIDOMWindowInner* inner = aDocument.GetInnerWindow()) {
    nsGlobalWindowInner* win = nsGlobalWindowInner::Cast(inner);
    if (win) {
      uint32_t ucid = mozilla::dom::ScreenDimensionManager::GetUserContextIdFromWindow(win);
      int32_t w = 0, h = 0;
      if (mozilla::dom::ScreenDimensionManager::GetDimensions(ucid, &w, &h)) {
        return CSSPixel::ToAppUnits(CSSIntSize(w, h));
      }
    }
  }
```

---

## 4. `layout/mathml/nsMathMLChar.cpp` — line 278 signature change

**Reason**: FF149 added a trailing `uint32_t aPrivateBrowsingId` parameter to the font-fallback
call. The patch stops at `nullptr)` so we append `, 0`.

**Exact command**:

```bash
sed -i '278s/nsTextFrameUtils::Flags(), nullptr);/nsTextFrameUtils::Flags(), nullptr, 0); \/\/ TODO: Extract private browsing ID/' layout/mathml/nsMathMLChar.cpp
```

**BEFORE** (line 278):

```cpp
  ... nsTextFrameUtils::Flags(), nullptr);
```

**AFTER**:

```cpp
  ... nsTextFrameUtils::Flags(), nullptr, 0); // TODO: Extract private browsing ID
```

---

## 5. `dom/system/NetworkGeolocationProvider.sys.mjs` — use CAMOU_CONFIG geolocation

**Reason**: Geolocation-spoofing patch rejected in FF149 due to the network-provider
result-handling block being refactored. Replaced the anchor block so the overrides go
through `ChromeUtils.camouGetDouble`.

**BEFORE**:

```javascript
    let result;
    try {
      result = await this.fetchLocation(url, wifiData);
      lazy.log.info(
        `geo provider reported: ${result.location.lng}:${result.location.lat}`
      );
      let newLocation = new NetworkGeoPositionObject(
        result.location.lat,
        result.location.lng,
        result.accuracy
      );

      if (this.listener) {
        this.listener.update(newLocation);
      }
```

**AFTER**:

```javascript
    // Use CAMOU_CONFIG geolocation if configured
    let DEFAULT_DEG = -180;

    let latitude = ChromeUtils.camouGetDouble("geolocation:latitude", DEFAULT_DEG);
    let longitude = ChromeUtils.camouGetDouble("geolocation:longitude", DEFAULT_DEG);
    let accuracy = ChromeUtils.camouGetDouble("geolocation:accuracy", 0);

    let countDecimalPlaces = (num) => {
      if (Math.floor(num) === num) return 0;
      return num.toString().split(".")[1].length || 0;
    }

    let result;
    try {
      let newLocation;
      if (latitude != DEFAULT_DEG && longitude != DEFAULT_DEG) {
        // Use the latitude and longitude from CAMOU_CONFIG
        ChromeUtils.camouDebug(
          `Use Camoufox geo: ${latitude}:${longitude} accuracy:${accuracy}`
        );
        // If accuracy is not set, calculate it using decimal precision
        if (!accuracy) {
          let latPrecision = countDecimalPlaces(latitude);
          let lonPrecision = countDecimalPlaces(longitude);
          let precision = Math.min(latPrecision, lonPrecision);
          // Estimate accuracy in meters
          accuracy = (111320 * Math.cos(latitude * Math.PI / 180)) / Math.pow(10, precision);
        }
        newLocation = new NetworkGeoPositionObject(
          latitude,
          longitude,
          accuracy
        );
      } else {
        // No CAMOU_CONFIG override, use real geolocation
        result = await this.fetchLocation(url, wifiData);

        lazy.log.info(
          `geo provider reported: ${result.location.lng}:${result.location.lat}`
        );
        newLocation = new NetworkGeoPositionObject(
          result.location.lat,
          result.location.lng,
          accuracy || result.accuracy
        );
      }

      if (this.listener) {
        this.listener.update(newLocation);
      }
```

---

## 6. `js/src/vm/DateTime.cpp` — two timezone fixes

### 6.1 Remove `IsValidTimeZoneId` (no longer exists in FF149)

**Reason**: `mozilla::intl::TimeZone::IsValidTimeZoneId` was removed from FF149's
`intl/components/src/TimeZone.h`. Fall back to a null/empty check.

**BEFORE**:

```cpp
  if (!timeZoneId || !mozilla::intl::TimeZone::IsValidTimeZoneId(timeZoneId)) {
    return false;
  }
```

**AFTER**:

```cpp
  if (!timeZoneId || !*timeZoneId) {
    return false;
  }
```

### 6.2 `std::string` → `RefPtr<JS::TimeZoneString>` for `timeZoneOverride_`

**Reason**: FF149 changed the type of `DateTimeInfo::timeZoneOverride_` from a bare
`std::string` to a refcounted `JS::TimeZoneString*`. The constructor has to allocate a new
`TimeZoneString` instead of assigning a std::string.

**BEFORE**:

```cpp
js::DateTimeInfo::DateTimeInfo(const char* timezone) : DateTimeInfo() {
#if JS_HAS_INTL_API
  timeZoneOverride_ = timezone ? std::string(timezone) : std::string();
#endif
}
```

**AFTER**:

```cpp
js::DateTimeInfo::DateTimeInfo(const char* timezone) : DateTimeInfo() {
#if JS_HAS_INTL_API
  if (timezone && *timezone) {
    timeZoneOverride_ = new JS::TimeZoneString(timezone);
  }
#endif
}
```

### 6.3 `setenv("TZ", ...)` → `_putenv_s("TZ", ...)` for Windows cross-compile

**Reason**: MSVC cross-compile does not have POSIX `setenv`. Applied via `sed`:

```bash
sed -i 's/setenv("TZ", timezoneId, 1);/_putenv_s("TZ", timezoneId);/' js/src/vm/DateTime.cpp
```

**BEFORE**:

```cpp
  setenv("TZ", timezoneId, 1);
```

**AFTER**:

```cpp
  _putenv_s("TZ", timezoneId);
```

---

## 7. `dom/media/webspeech/synth/` — voice-spoofing manual inline reapply

The rewritten `voice-spoofing.patch` was partly re-applied via a Python heredoc because
hunks still failed. The final state of both files is:

### 7.1 `dom/media/webspeech/synth/moz.build` — append LOCAL_INCLUDES

**AFTER** (append to end of file):

```python
# DOM Mask
LOCAL_INCLUDES += ['/camoucfg']
```

### 7.2 `dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp` — 3 insertions

**Insertion 1 — include**

After line: `#include "nsString.h"`

```cpp
#include "nsString.h"

#include "MaskConfig.hpp"
using mozilla::intl::LocaleService;
```

**Insertion 2 — voice loader after `NS_SPEECH_SYNTH_STARTED`**

Find:

```cpp
      NS_CreateServicesFromCategory(NS_SPEECH_SYNTH_STARTED, nullptr,
                                    NS_SPEECH_SYNTH_STARTED);
```

Append after it:

```cpp
      // Load voices from MaskConfig
      if (auto voices = MaskConfig::MVoices()) {
        for (const auto& [lang, name, uri, isDefault, isLocal] :
             voices.value()) {
          gSynthVoiceRegistry->AddVoiceImpl(
              nullptr, NS_ConvertUTF8toUTF16(uri), NS_ConvertUTF8toUTF16(name),
              NS_ConvertUTF8toUTF16(lang), isLocal,
              false);  // queuesUtterances set to false
          if (isDefault) {
            gSynthVoiceRegistry->SetDefaultVoice(NS_ConvertUTF8toUTF16(uri),
                                                 true);
          }
        }
      }
```

**Insertion 3 — `blockIfNotDefined` early return in `AddVoice`**

Find:

```cpp
  return AddVoiceImpl(aService, aUri, aName, aLang, aLocalService,
                      aQueuesUtterances);
```

Replace with:

```cpp
  if (MaskConfig::GetBool("voices:blockIfNotDefined")) return NS_OK;

  return AddVoiceImpl(aService, aUri, aName, aLang, aLocalService,
                      aQueuesUtterances);
```

**Insertion 4 — fake voice block in `SpeakImpl`**

Find marker line (note the leading 7 spaces, not 6 — this is the Python heredoc's exact text):

```cpp
       NS_ConvertUTF16toUTF8(aVoice->mUri).get(), aRate, aPitch));
```

Append after it (one blank line, then the block):

```cpp
  // Check if this voice is in our MVoices config
  if (auto voices = MaskConfig::MVoices()) {
    for (const auto& [lang, name, uri, isDefault, isLocal] : voices.value()) {
      if (NS_ConvertUTF8toUTF16(uri).Equals(aVoice->mUri)) {
        printf_stderr("Tried to speak a fake voice: %s",
                      NS_ConvertUTF16toUTF8(aVoice->mUri).get());
        aTask->Init();
        if (!MaskConfig::GetBool("voices:fakeCompletion")) {
          aTask->DispatchError(0, 0);
          return;
        }
        float charsPerSecond;
        if (auto value =
                MaskConfig::GetDouble("voices:fakeCompletion:charsPerSecond")) {
          charsPerSecond = value.value();
        } else {
          charsPerSecond = 12.5f;
        }
        aTask->DispatchStart();
        float fakeElapsedTime =
            static_cast<float>(aText.Length()) / (charsPerSecond * aRate);
        aTask->DispatchEnd(fakeElapsedTime, aText.Length());
        return;
      }
    }
  }
```

---

## 8. `netwerk/protocol/http/Http2Session.h` — new settings-type enum value

**Reason**: H2 fingerprint-spoofing patch reject. FF149 has a `// 6 is SETTINGS_TYPE_MAX_HEADER_LIST - advisory, we ignore it` comment where the old enum value was; we replace it with a real enum entry.

**BEFORE** (one of two variants — try both, the Python heredoc tried the second first, then a grep fallback):

```cpp
    // 6 is SETTINGS_TYPE_MAX_HEADER_LIST - advisory, we ignore it
```

OR

```cpp
    SETTINGS_TYPE_ENABLE_CONNECT_PROTOCOL = 8,
    SETTINGS_NO_RFC7540_PRIORITIES = 9,
```

**AFTER** (matching variant):

```cpp
    SETTINGS_TYPE_MAX_HEADER_LIST_SIZE = 6,
```

OR

```cpp
    SETTINGS_TYPE_ENABLE_CONNECT_PROTOCOL = 8,
    SETTINGS_TYPE_MAX_HEADER_LIST_SIZE = 6,
    SETTINGS_NO_RFC7540_PRIORITIES = 9,
```

---

## 9. `netwerk/protocol/http/Http2Session.cpp` — full H2 fingerprint spoofing

This was the biggest reject. Applied via Python heredoc. All substitutions in order:

### 9.1 Add MaskConfig include (two attempts — one worked)

```cpp
// First attempt:
#include "Http2Push.h"
#include "MaskConfig.hpp"

#include "mozilla/EndianUtils.h"
```

Fallback actually used:

```cpp
#include "AltServiceChild.h"
#include "MaskConfig.hpp"
```

### 9.2 `maxSettings 6 -> 8`

**BEFORE**:
```cpp
static const uint32_t maxSettings = 6;
```
**AFTER**:
```cpp
static const uint32_t maxSettings = 8;
```

### 9.3 Configurable `HEADER_TABLE_SIZE`

**BEFORE**:
```cpp
  uint32_t maxHpackBufferSize = gHttpHandler->DefaultHpackBuffer();
```
**AFTER**:
```cpp
  // Camoufox: configurable HTTP/2 fingerprint
  auto cfgHeaderTableSize = MaskConfig::GetUint64("h2:headerTableSize");
  uint32_t maxHpackBufferSize = cfgHeaderTableSize
      ? static_cast<uint32_t>(cfgHeaderTableSize.value())
      : gHttpHandler->DefaultHpackBuffer();
```

### 9.4 Configurable `ENABLE_PUSH`

**BEFORE** (try primary, fallback to shorter form):
```cpp
  // We don't support HTTP/2 Push. Set SETTINGS_TYPE_ENABLE_PUSH to 0
  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),
                             SETTINGS_TYPE_ENABLE_PUSH);
  // value is 0 because memset zeroed the buffer
  numberOfEntries++;
```

**AFTER**:
```cpp
  // Camoufox: configurable ENABLE_PUSH
  auto cfgEnablePush = MaskConfig::GetUint64("h2:enablePush");
  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),
                             SETTINGS_TYPE_ENABLE_PUSH);
  if (cfgEnablePush) {
    NetworkEndian::writeUint32(
        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,
        static_cast<uint32_t>(cfgEnablePush.value()));
  }
  numberOfEntries++;
```

### 9.5 Configurable `MAX_CONCURRENT_STREAMS`

**BEFORE** (primary or fallback):
```cpp
  if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {
    NetworkEndian::writeUint16(
        packet + kFrameHeaderBytes + (6 * numberOfEntries),
        SETTINGS_TYPE_MAX_CONCURRENT);
    // value is 0 (from memset)
    numberOfEntries++;
  }
```

**AFTER**:
```cpp
  // Camoufox: configurable MAX_CONCURRENT_STREAMS
  auto cfgMaxConcurrent = MaskConfig::GetUint64("h2:maxConcurrentStreams");
  if (cfgMaxConcurrent) {
    NetworkEndian::writeUint16(
        packet + kFrameHeaderBytes + (6 * numberOfEntries),
        SETTINGS_TYPE_MAX_CONCURRENT);
    NetworkEndian::writeUint32(
        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,
        static_cast<uint32_t>(cfgMaxConcurrent.value()));
    numberOfEntries++;
  } else if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {
    NetworkEndian::writeUint16(
        packet + kFrameHeaderBytes + (6 * numberOfEntries),
        SETTINGS_TYPE_MAX_CONCURRENT);
    numberOfEntries++;
  }
```

### 9.6 Configurable `INITIAL_WINDOW_SIZE`

**BEFORE**:
```cpp
  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),
                             SETTINGS_TYPE_INITIAL_WINDOW);
  NetworkEndian::writeUint32(
      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, mPushAllowance);
```

**AFTER**:
```cpp
  // Camoufox: configurable INITIAL_WINDOW_SIZE
  auto cfgInitialWindow = MaskConfig::GetUint64("h2:initialWindowSize");
  uint32_t initialWindowValue = cfgInitialWindow
      ? static_cast<uint32_t>(cfgInitialWindow.value())
      : mPushAllowance;
  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),
                             SETTINGS_TYPE_INITIAL_WINDOW);
  NetworkEndian::writeUint32(
      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, initialWindowValue);
```

### 9.7 Add `MAX_HEADER_LIST_SIZE` after `MAX_FRAME_SIZE`

**BEFORE**:
```cpp
      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, kMaxFrameData);
  numberOfEntries++;

  bool disableRFC7540Priorities
```

**AFTER**:
```cpp
      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, kMaxFrameData);
  numberOfEntries++;

  // Camoufox: optional MAX_HEADER_LIST_SIZE (Chrome sends 262144)
  auto cfgMaxHeaderListSize = MaskConfig::GetUint64("h2:maxHeaderListSize");
  if (cfgMaxHeaderListSize) {
    NetworkEndian::writeUint16(
        packet + kFrameHeaderBytes + (6 * numberOfEntries),
        SETTINGS_TYPE_MAX_HEADER_LIST_SIZE);
    NetworkEndian::writeUint32(
        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,
        static_cast<uint32_t>(cfgMaxHeaderListSize.value()));
    numberOfEntries++;
  }

  bool disableRFC7540Priorities
```

### 9.8 Configurable `WINDOW_UPDATE` size

**BEFORE**:
```cpp
  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;
```
**AFTER**:
```cpp
  // Camoufox: configurable WINDOW_UPDATE size
  auto cfgWindowUpdate = MaskConfig::GetUint64("h2:windowUpdateSize");
  if (cfgWindowUpdate) {
    mInitialRwin = static_cast<uint32_t>(cfgWindowUpdate.value()) + kDefaultRwin;
  }
  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;
```

### 9.9 Configurable priority-frame disable

**BEFORE**:
```cpp
  if (!disableRFC7540Priorities) {
    mUseH2Deps = true;
```
**AFTER**:
```cpp
  // Camoufox: allow disabling priority frames
  bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority", false);
  if (!disableRFC7540Priorities && !cfgDisablePriority) {
    mUseH2Deps = true;
```

### 9.10 `MaskConfig::GetBool` returns `optional<bool>` — fix call site

Two sequential sed commands were needed to reconcile the fact that
`MaskConfig::GetBool(key, default)` does NOT exist (`GetBool` takes only one arg and
returns `std::optional<bool>`).

First (strip the second arg):

```bash
sed -i 's/MaskConfig::GetBool("h2:disablePriority", false)/MaskConfig::GetBool("h2:disablePriority")/' \
    netwerk/protocol/http/Http2Session.cpp
```

Then (apply `.value_or(false)`):

```bash
sed -i 's/bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority");/bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority").value_or(false);/' \
    netwerk/protocol/http/Http2Session.cpp
```

**Final form of that line**:

```cpp
  bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority").value_or(false);
```

> **Reason**: `additions/camoucfg/MaskConfig.hpp::GetBool` in Camoufox returns
> `std::optional<bool>`, not `bool`. Every call site that wants a default needs
> `.value_or(false)`. This is the same root cause for issue (4) in the session notes
> ("MaskConfig::GetBool returns optional<bool>, needs .value_or(false)").

---

## 10. `netwerk/protocol/http/Http2Compression.cpp` — configurable pseudo-header order

### 10.1 Add include

**BEFORE**:
```cpp
#include "Http2HuffmanOutgoing.h"

#include "mozilla/StaticPrefs_network.h"
```
**AFTER**:
```cpp
#include "Http2HuffmanOutgoing.h"
#include "MaskConfig.hpp"

#include "mozilla/StaticPrefs_network.h"
```

(Fallback also used: after `#include "Http2Compression.h"` if the `Http2HuffmanOutgoing.h`
form was not found.)

### 10.2 Pseudo-header order loop

**BEFORE**:
```cpp
    ProcessHeader(nvPair(":method"_ns, method), false, false);
    ProcessHeader(nvPair(":path"_ns, path), true, false);
    ProcessHeader(nvPair(":authority"_ns, host), false, false);
    ProcessHeader(nvPair(":scheme"_ns, scheme), false, false);
```

**AFTER**:
```cpp
    // Camoufox: configurable pseudo-header order
    // Firefox default: "mpas" (method, path, authority, scheme)
    // Chrome default: "masp" (method, authority, scheme, path)
    auto cfgOrder = MaskConfig::GetString("h2:pseudoHeaderOrder");
    std::string order = cfgOrder ? cfgOrder.value() : "mpas";
    for (char c : order) {
      switch (c) {
        case 'm':
          ProcessHeader(nvPair(":method"_ns, method), false, false);
          break;
        case 'p':
          ProcessHeader(nvPair(":path"_ns, path), true, false);
          break;
        case 'a':
          ProcessHeader(nvPair(":authority"_ns, host), false, false);
          break;
        case 's':
          ProcessHeader(nvPair(":scheme"_ns, scheme), false, false);
          break;
      }
    }
```

---

## 11. `dom/base/Navigator.cpp` — timezone-override runtime fix

**Reason**: The existing per-context patch called `nsJSUtils::SetTimeZoneOverride(...)`,
which no longer exists in FF149. Replaced with a direct `setenv` + `JS::ResetTimeZone()`
call. Also the include line had to be switched from `js/public/Date.h` to `js/Date.h`
because `js/public/Date.h` is not on the include path from `dom/base/`.

### 11.1 Replace the call

**BEFORE**:
```cpp
    nsJSUtils::SetTimeZoneOverride(tz.value().c_str());
```

**AFTER**:
```cpp
    setenv("TZ", tz.value().c_str(), 1);
    JS::ResetTimeZone();
```

### 11.2 Later — `setenv` → `_putenv_s` for Windows

```bash
sed -i 's/setenv("TZ", tz.value().c_str(), 1);/_putenv_s("TZ", tz.value().c_str());/' \
    dom/base/Navigator.cpp
```

**FINAL**:
```cpp
    _putenv_s("TZ", tz.value().c_str());
    JS::ResetTimeZone();
```

### 11.3 Add the `js/Date.h` include

Two sequential steps were executed:

Step 1 — first added `js/public/Date.h` just after `MaskConfig.hpp` include:

```bash
sed -i '/#include "MaskConfig.hpp"/a #include "js/public/Date.h"' dom/base/Navigator.cpp
```

Step 2 — then rewrote it to the actually-correct path because `dom/base/` can't see
`js/public/` but has `js/Date.h` on its include path:

```bash
sed -i 's|#include "js/public/Date.h"|#include "js/Date.h"|' dom/base/Navigator.cpp
```

**FINAL** (just after the `#include "MaskConfig.hpp"` line):

```cpp
#include "MaskConfig.hpp"
#include "js/Date.h"
```

---

## 12. `gfx/thebes/gfxPlatformFontList.cpp` — add `GetDocument()` pure-virtual override

**Reason**: FF149 added `virtual Document* GetDocument() const = 0;` to the
`FontVisibilityProvider` base class. Any class inheriting from it must now implement the
override. The `ListFontsVisibilityProvider` (or similar) inner class was missing it.

**BEFORE**:
```cpp
  void UserFontSetUpdated(gfxUserFontEntry*) override {}
  using FontVisibilityProvider::ReportBlockedFontFamily;
```

**AFTER**:
```cpp
  void UserFontSetUpdated(gfxUserFontEntry*) override {}
  mozilla::dom::Document* GetDocument() const override { return nullptr; }
  using FontVisibilityProvider::ReportBlockedFontFamily;
```

> **Note**: The session notes list "GetDocument() pure virtual added in FF149
> FontVisibilityProvider" as issue #7. This is the only file where the fix was applied in
> this JSONL. If a build error complains about `GetDocument` being pure virtual in
> `dom/workers/WorkerPrivate.h` or `dom/html/OffscreenCanvas.h` (as the Priority-2 list
> suggested), the same one-line override (`return nullptr;`) should be added to those
> classes too — but those edits do NOT appear in this transcript.

---

## 13. `additions/browser/branding/camoufox/configure.sh` (additions tree, NOT in-source)

After the initial configure was done with `MOZ_APP_NAME=firefox` (required by the UA fix
trick), `MOZ_APP_NAME` was reverted back to `camoufox`, then copied into the source tree
and rebuilt:

```bash
# 1) Edit additions tree
sed -i 's/MOZ_APP_NAME=firefox/MOZ_APP_NAME=camoufox/' \
    additions/browser/branding/camoufox/configure.sh

# 2) Copy into source tree
cp ../additions/browser/branding/camoufox/configure.sh \
   browser/branding/camoufox/configure.sh
```

**FINAL content of `browser/branding/camoufox/configure.sh`** — contains
`MOZ_APP_NAME=camoufox`. The UA stays `Firefox` because of the `MOZ_APP_UA_NAME` imply
from fix 0.3 above.

> **Reason**: The packaging step names the zip after `MOZ_APP_NAME`, and internal Camoufox
> tooling (`package.py`, `pkgman.py`) looks for `camoufox-*.zip` and `camoufox.exe`. The
> packaging also hard-copies `firefox.exe` → `camoufox.exe` post-build (see fix 14).

---

## 14. `dom/base/moz.build` — `NavigatorManager.cpp` placement

**Reason**: `NavigatorManager.cpp` was inadvertently listed under `EXPORTS` instead of
`UNIFIED_SOURCES`, causing unresolved externals (`IsPlatformFunctionEnabledForWebIDL`, etc.)
at link time.

Two-step fix (both via Python heredoc):

### Step 1 — original (wrong) insert

```python
# Fix 1: Add NavigatorManager.cpp to UNIFIED_SOURCES in dom/base/moz.build
with open('dom/base/moz.build', 'r') as f:
    c = f.read()
if '"NavigatorManager.cpp"' not in c:
    c = c.replace('    "NavigatorManager.h",',
                  '    "NavigatorManager.cpp",\n    "NavigatorManager.h",')
    with open('dom/base/moz.build', 'w') as f:
        f.write(c)
```

This placed `NavigatorManager.cpp` in the `EXPORTS` list (next to the `.h`), which was wrong.

### Step 2 — fix (relocate to UNIFIED_SOURCES)

```python
with open('dom/base/moz.build', 'r') as f:
    c = f.read()

# Remove from EXPORTS section
c = c.replace('    "NavigatorManager.cpp",\n    "NavigatorManager.h",',
              '    "NavigatorManager.h",')

# Add to UNIFIED_SOURCES (after Navigator.cpp)
c = c.replace('    "Navigator.cpp",',
              '    "Navigator.cpp",\n    "NavigatorManager.cpp",')

with open('dom/base/moz.build', 'w') as f:
    f.write(c)
```

**FINAL state (EXPORTS block)**:

```python
EXPORTS += [
    ...
    "NavigatorManager.h",
    ...
]
```

**FINAL state (UNIFIED_SOURCES block)**:

```python
UNIFIED_SOURCES += [
    ...
    "Navigator.cpp",
    "NavigatorManager.cpp",
    ...
]
```

---

## 15. `dom/media/webspeech/synth/moz.build` — append `LOCAL_INCLUDES`

(Already listed under 7.1 — repeated here for completeness of the file-by-file index.)

```python
# DOM Mask
LOCAL_INCLUDES += ['/camoucfg']
```

---

## 16. `pythonlib/camoufox/extension/experiment/api.js` (non-FF149 source, but was a runtime fix)

**Reason**: `WheelEvent.DOM_DELTA_PIXEL` is not accessible from the privileged
extension-experiment context in FF149. Replaced with the literal value `0`.

**File**: `C:\Users\leone\OneDrive\Desktop\Programacion\Trabajo\camoufox\pythonlib\camoufox\extension\experiment\api.js`

**BEFORE**:
```javascript
          utils.sendWheelEvent(
            rect.left + x, rect.top + y,
            deltaX, deltaY, 0,
            WheelEvent.DOM_DELTA_PIXEL,
            0, 0, 0, 0
          );
```

**AFTER**:
```javascript
          utils.sendWheelEvent(
            rect.left + x, rect.top + y,
            deltaX, deltaY, 0,
            0, /* DOM_DELTA_PIXEL */
            0, 0, 0, 0
          );
```

---

## 17. Post-build packaging steps (not strictly source edits, but required)

After `./mach build` succeeds and `./mach install` has run, the following must be done
to make the artifact consumable by Camoufox's pythonlib:

```bash
# Rename zip to camoufox-* so package.py can find it
cd obj-x86_64-pc-windows-msvc/dist
cp firefox-149.0-beta.1.en-US.win64.zip camoufox-149.0-beta.1.en-US.win64.zip

# After unzipping at install target:
#   The exe is inside a firefox/ subdir -- flatten:
mv firefox/* .
rmdir firefox

# Pythonlib expects camoufox.exe, but the binary is firefox.exe -- copy it:
cp firefox.exe camoufox.exe
```

And `version.json` must be created at the install root:

```json
{"version":"149.0","build":"beta.1","prerelease":false,"asset_id":null,"asset_size":null,"asset_updated_at":null}
```

---

## File index (for quick reference)

| # | File | Kind |
|---|---|---|
| 0.3 | `patches/librewolf/disable-data-reporting-at-compile-time.patch` | Patch rewrite (UA fix) |
| 0.4 | `patches/voice-spoofing.patch` | Patch rewrite |
| 1 | `dom/base/nsGlobalWindowInner.h` | Add 3 method decls |
| 2 | `js/public/Date.h` | Add 2 `extern` decls |
| 3 | `layout/style/nsMediaFeatures.cpp` | Replace RDM/BiDi block |
| 4 | `layout/mathml/nsMathMLChar.cpp` | Line 278 signature fix |
| 5 | `dom/system/NetworkGeolocationProvider.sys.mjs` | CAMOU_CONFIG geo |
| 6.1 | `js/src/vm/DateTime.cpp` | Remove `IsValidTimeZoneId` |
| 6.2 | `js/src/vm/DateTime.cpp` | `std::string` → `RefPtr<TimeZoneString>` |
| 6.3 | `js/src/vm/DateTime.cpp` | `setenv` → `_putenv_s` |
| 7.1 | `dom/media/webspeech/synth/moz.build` | `LOCAL_INCLUDES += ['/camoucfg']` |
| 7.2 | `dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp` | 4 inserts |
| 8 | `netwerk/protocol/http/Http2Session.h` | Enum `MAX_HEADER_LIST_SIZE=6` |
| 9 | `netwerk/protocol/http/Http2Session.cpp` | 9 substitutions (H2 fingerprint) |
| 10 | `netwerk/protocol/http/Http2Compression.cpp` | Pseudo-header order |
| 11.1 | `dom/base/Navigator.cpp` | `nsJSUtils::SetTimeZoneOverride` → `setenv`+`ResetTimeZone` |
| 11.2 | `dom/base/Navigator.cpp` | `setenv` → `_putenv_s` |
| 11.3 | `dom/base/Navigator.cpp` | `#include "js/Date.h"` |
| 12 | `gfx/thebes/gfxPlatformFontList.cpp` | `GetDocument()` override |
| 13 | `browser/branding/camoufox/configure.sh` | `MOZ_APP_NAME=camoufox` |
| 14 | `dom/base/moz.build` | Move `NavigatorManager.cpp` from EXPORTS to UNIFIED_SOURCES |
| 15 | `dom/media/webspeech/synth/moz.build` | (duplicate of 7.1) |
| 16 | `pythonlib/camoufox/extension/experiment/api.js` | `WheelEvent.DOM_DELTA_PIXEL` → `0` |
| 17 | (packaging) | zip/exe rename, `version.json` |

---

## Fixes NOT found in this transcript (potentially from earlier sessions)

The user's request listed these Priority-2 items. They are **NOT present** in
`e19e7540-f80a-4bf2-97b9-f3fca4e93c57.jsonl` and must be re-derived or fetched from
another JSONL if still needed:

- `dom/base/nsGlobalWindowInner.h` — moving `SetWebGLVendor`/`SetWebGLRenderer` from
  `protected` to `public` (only `SetAudioFingerprintSeed`/`SetScreenDimensions`/
  `SetScreenColorDepth` decls were added in this session).
- `dom/workers/WorkerPrivate.h` — `GetDocument()` declaration/override.
- `dom/html/OffscreenCanvas.h` — `GetDocument()` declaration/override.
- `dom/canvas/ClientWebGLContext.cpp` — webgl-spoofing patch rejected hunk (the transcript
  grepped for `GetParameter.*DEPTH_RANGE|blockIfNotDefined` but no actual edit was made in
  this session — the patch was already cleanly applied or handled earlier).
- `js/src/vm/Realm.cpp`, `js/src/vm/Realm.h` — timezone `setTimeZoneOverride` method
  (the transcript did `grep -n 'setTimeZoneOverride'` but made no edit).
- `toolkit/xre/nsXREDirProvider.cpp` — not touched in this session.
- `browser/components/preferences/main.js` — not touched.
- `browser/app/Makefile.in` — not touched.
- `patches/chromeutil.patch` — `printf_stderr("DEBUG: ...")` removal — not touched.
- `settings/camoufox.cfg` — not touched as a manual fix (only read for diagnostics).
- `additions/camoucfg/MaskConfig.hpp` — not edited (the `.value_or(false)` workaround was
  applied at call sites in `Http2Session.cpp` — see 9.10 — not in MaskConfig itself).
- `additions/juggler/api.js` — only the pythonlib extension `api.js` in (16) was touched.

If any of these are actually needed, check `d1ffeee2-c908-48ff-af74-086ec0a4ea0f.jsonl`
(Mar 27 19:45) or `06d388b9-bebe-495a-b4e1-b308f06f6c15.jsonl` (Mar 27 19:49) — those
are the two earlier JSONLs from the same build day.

---

## Re-apply recipe (for a fresh FF149 source tree)

```bash
# In /app/camoufox-149.0-beta.1 after patches have been applied and .rej files exist:

# 1. Fix layout/mathml/nsMathMLChar.cpp
sed -i '278s/nsTextFrameUtils::Flags(), nullptr);/nsTextFrameUtils::Flags(), nullptr, 0); \/\/ TODO: Extract private browsing ID/' \
    layout/mathml/nsMathMLChar.cpp

# 2. Add per-context setter declarations to nsGlobalWindowInner.h
sed -i '/mozilla::dom::Worklet\* GetPaintWorklet(mozilla::ErrorResult& aRv);/{
a\
\
  // Audio fingerprint seed for per-context audio fingerprinting\
  void SetAudioFingerprintSeed(uint32_t seed, mozilla::ErrorResult\& aRv);\
\
  // Screen dimension methods for per-context screen spoofing\
  void SetScreenDimensions(int32_t aWidth, int32_t aHeight, mozilla::ErrorResult\& aRv);\
  void SetScreenColorDepth(int32_t aColorDepth, mozilla::ErrorResult\& aRv);
}' dom/base/nsGlobalWindowInner.h

# 3. Add timezone externs to js/public/Date.h
sed -i '/^extern JS_PUBLIC_API void ResetTimeZone();$/a\
\
extern JS_PUBLIC_API bool SetTimeZoneOverride(const char* timezoneId);\
// Camoufox: per-context timezone override\
extern JS_PUBLIC_API bool SetRealmTimeZoneOverride(JSContext* cx, const char* timezoneId);' js/public/Date.h

# 4-12. Apply the Python heredoc blocks from sections 3, 5, 6, 7, 9, 10, 11, 12, 14
#       via `python3 << PYEOF ... PYEOF` in the source root.

# 13. Fix disablePriority call site twice (optional<bool>)
sed -i 's/MaskConfig::GetBool("h2:disablePriority", false)/MaskConfig::GetBool("h2:disablePriority")/' \
    netwerk/protocol/http/Http2Session.cpp
sed -i 's/bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority");/bool cfgDisablePriority = MaskConfig::GetBool("h2:disablePriority").value_or(false);/' \
    netwerk/protocol/http/Http2Session.cpp

# 14. Windows cross-compile: setenv -> _putenv_s in both files
sed -i 's/setenv("TZ", timezoneId, 1);/_putenv_s("TZ", timezoneId);/' js/src/vm/DateTime.cpp
sed -i 's/setenv("TZ", tz.value().c_str(), 1);/_putenv_s("TZ", tz.value().c_str());/' dom/base/Navigator.cpp

# 15. Navigator.cpp include
sed -i '/#include "MaskConfig.hpp"/a #include "js/Date.h"' dom/base/Navigator.cpp

# 16. Build
./mach build
./mach install
```

---

_Generated from transcript_ `e19e7540-f80a-4bf2-97b9-f3fca4e93c57.jsonl`
_(44 MB, 2651 events — session of 2026-03-27, Camoufox v149 build)_

---
---

# Additional fixes from Mar 29 rebuild session

Sources:
- `87fce7f2-7c42-42b5-bbac-b68da5a92aa2.jsonl` (5.5 MB, 1394 events, Mar 29 13:07) — patch-level
  pre-container edits (host-side `patches/` dir)
- `aab7362b-b1ac-40fb-ad37-d060b6467522.jsonl` (4.0 MB, 1870 events, Mar 29 21:53) — in-container
  source edits and `settings/camoufox.cfg` audit
- `06d388b9-bebe-495a-b4e1-b308f06f6c15.jsonl` (10 MB, 3562 events, Mar 27 19:49) — one late
  Mar 27 evening fix to `toolkit/components/remote/nsRemoteService.cpp` that Mar 27 doc missed
- `d1ffeee2-c908-48ff-af74-086ec0a4ea0f.jsonl` (407 KB, 132 events, Mar 27 19:45) — **no
  file-modifying events relevant to the v149 source tree**; this session was pure conversation.

Scope of this section: the Mar 27 doc already covers the primary v149 source-tree edits
(`Http2Session`, `Navigator.cpp`, `nsMathMLChar.cpp`, etc.). Mar 29 is about:
1. Cleaning up host-side `patches/*.patch` files of DEBUG printf leaks and rebrand leftovers
2. Aligning `settings/camoufox.cfg` with stock Firefox (remove detection vectors, fix 15 prefs)
3. New in-container fixes that were NOT reached on Mar 27: webgl dedupe / protected→public
   move / `GetDocument()` in worker + offscreen / full `gfxTextRun.h` userContextId plumbing /
   `JS::SetTimeZoneOverride` body insertion / `nsRemoteService.cpp` multi-instance / broken
   `Cargo.toml.orig` restore / `MaskConfig::GetBool`→`CheckBool` rename in four files
4. Priority-2 rejects (`js/src/vm/Realm.cpp`, `dom/canvas/ClientWebGLContext.cpp`) that the
   Mar 27 recovery doc explicitly flagged as MISSING — Mar 29 handled both.

Paths without a leading `/` prefix are host-side files in the local
`camoufox` repo. Paths beginning with `/app/camoufox-149.0-beta.1/` are in-container source
edits executed via `docker exec cfox ...`.

---

## A1. `patches/chromeutil.patch` — remove `printf_stderr("DEBUG: ...")` leak

**Reason**: The patch as shipped contained a debug `printf_stderr` call that leaked to
`stderr` during every `ChromeUtils::ImportESModule` call — visible in the process log and
(more importantly) detectable via any extension that reads the Browser Console. The leak
was left over from the Mar 27 build when the original timezone debugging was being done.

Host copy (Mar 29 event #117 verified by event #1009 which did
`docker cp cfox:/app/patches/chromeutil.patch` back to the host repo, so the fix lives on
disk in the local repo).

In-container fix (aab7362b event #100):

```bash
docker exec cfox python3 << 'PYEOF'
p = '/app/patches/chromeutil.patch'
with open(p) as f:
    lines = f.readlines()
new_lines = [l for l in lines if 'printf_stderr("DEBUG:' not in l]
with open(p, 'w') as f:
    f.writelines(new_lines)
print(f"Removed {len(lines) - len(new_lines)} line(s)")
PYEOF
```

The line that was removed:

```c
+  printf_stderr("DEBUG: %s\n", utf8VarName.get());
```

**After re-applying this cleaned `chromeutil.patch`, `./mach build` produces a binary that
no longer writes anything to stderr at ChromeUtils load time.**

---

## A2. `patches/librewolf/devtools-bypass.patch` — rebrand `librewolf.*` → `camoufox.*`

**Reason**: Two pref names in the devtools-bypass patch still used the `librewolf.*`
namespace. Anti-bot stacks (and the DevTools console itself) can inspect `about:config`
and flag `librewolf.*` prefs as proof of a non-vanilla browser.

Host-side edits (87fce7f2 events #240 and #243):

```diff
-librewolf.debugger.force_detach
+camoufox.debugger.force_detach
```

```diff
-librewolf.console.logging_disabled
+camoufox.console.logging_disabled
```

Event #1362 then `docker cp`'d the fixed patch into `cfox:/app/patches/librewolf/devtools-bypass.patch`.

---

## A3. `patches/anti-font-fingerprinting.patch` — remove 2 debug `printf` leaks

**Reason**: Same concern as A1 — raw `printf(...)` calls inside `gfxHarfBuzzShaper.cpp` and
`gfxTextRun.cpp` that would have shipped in the release binary if the patch was applied as-is.

Host-side edits (87fce7f2 events #1320 and #1371).

### A3.1 Remove HarfBuzzShaper debug printf

**BEFORE** (lines inside the patch context):

```
+  if (!seedFromManager) {
+    seed = 0x6D2B79F5u; // fixed constant to avoid time-based variance
+  }
+  printf("HarfBuzzShaper: pbid=%u seed=%u (from_manager=%d)\n",
+         pbid,
+         seed,
+         seedFromManager ? 1 : 0);
+
+  // Generate a random float [0, 0.1] to offset the letter spacing
```

**AFTER**:

```
+  if (!seedFromManager) {
+    seed = 0x6D2B79F5u; // fixed constant to avoid time-based variance
+  }
+
+  // Generate a random float [0, 0.1] to offset the letter spacing
```

### A3.2 Remove MakeTextRun debug printf

**BEFORE**:

```
+  // Log the user context id used for this text run creation
+  printf("MakeTextRun: userContextId=%u\n", aUserContextId);
+
   RefPtr<gfxTextRun> textRun =
```

**AFTER**:

```
+
   RefPtr<gfxTextRun> textRun =
```

Event #1362 `docker cp`'d the patch into the container.

---

## A4. `settings/camoufox.cfg` — remove two detection vectors

**Reason**: These two prefs are not present in a stock Firefox 149 profile. Any pref that
only ships with automation frameworks is a direct fingerprint. Removed by aab7362b
event #117.

**Removed lines** (ALL `defaultPref` lines matching these keys were filtered out):

```javascript
defaultPref("dom.input_events.security.minNumTicks", 0);
defaultPref("focusmanager.testmode", true);
```

Executed in-container:

```bash
docker exec cfox bash -c "python3 -c \"
with open('/app/settings/camoufox.cfg') as f:
    content = f.read()
lines = content.split('\\n')
lines = [l for l in lines if 'dom.input_events.security.minNumTicks' not in l]
lines = [l for l in lines if 'focusmanager.testmode' not in l]
with open('/app/settings/camoufox.cfg', 'w') as f:
    f.write('\\n'.join(lines))
print('Done')
\""
```

Then `docker cp cfox:/app/settings/camoufox.cfg` back to the host (event #1009), so the
fixed cfg lives in the local repo.

---

## A5. `settings/camoufox.cfg` — `browser.sessionhistory.max_entries` 0 → 50

**Reason**: `max_entries=0` disables the session history entirely. Stock Firefox uses 50.
A detection probe that calls `history.length` after a few navigations sees a suspicious `0`.

Same event (#117):

```javascript
// BEFORE
defaultPref("browser.sessionhistory.max_entries", 0);
// AFTER
defaultPref("browser.sessionhistory.max_entries", 50);
```

---

## A6. `settings/camoufox.cfg` — 15 web-detectable prefs aligned to stock Firefox

**Reason**: aab7362b session ran an `audit_prefs.py` script (ephemeral, deleted at event #1107)
that diffed the `camoufox.cfg` against an `about:config` dump from stock Firefox 149. These
15 prefs were web-detectable (readable from JS via `navigator.*`, `matchMedia`, network
timing, etc.) and did not match stock. Fixed in event #942.

| Pref | OLD | NEW |
| --- | --- | --- |
| `dom.input_events.security.minTimeElapsedInMS` | `0` | `100` |
| `gfx.color_management.mode` | `0` | `2` |
| `gfx.color_management.rendering_intent` | `3` | `0` |
| `ui.use_standins_for_native_colors` | `true` | `false` |
| `media.autoplay.default` | `0` | `1` |
| `privacy.partition.network_state` | `false` | `true` |
| `dom.max_script_run_time` | `0` | `10` |
| `fission.bfcacheInParent` | `false` | `true` |
| `geo.provider.testing` | `true` | `false` |
| `dom.disable_open_during_load` | `false` | `true` |
| `network.cookie.cookieBehavior` | `4` | `5` |
| `network.http.speculative-parallel-limit` | `0` | `20` |
| `security.fileuri.strict_origin_policy` | `false` | `true` |
| `dom.push.connection.enabled` | `false` | `true` |
| `dom.push.serverURL` | `""` | `"wss://push.services.mozilla.com/"` |

Full in-container fixer script (handles both `defaultPref(...)` and `pref(...)` forms,
numeric/boolean/string values):

```bash
docker exec cfox bash -c "python3 << 'PYEOF'
fixes = {
    'dom.input_events.security.minTimeElapsedInMS': ('0', '100'),
    'gfx.color_management.mode': ('0', '2'),
    'gfx.color_management.rendering_intent': ('3', '0'),
    'ui.use_standins_for_native_colors': ('true', 'false'),
    'media.autoplay.default': ('0', '1'),
    'privacy.partition.network_state': ('false', 'true'),
    'dom.max_script_run_time': ('0', '10'),
    'fission.bfcacheInParent': ('false', 'true'),
    'geo.provider.testing': ('true', 'false'),
    'dom.disable_open_during_load': ('false', 'true'),
    'network.cookie.cookieBehavior': ('4', '5'),
    'network.http.speculative-parallel-limit': ('0', '20'),
    'security.fileuri.strict_origin_policy': ('false', 'true'),
    'dom.push.connection.enabled': ('false', 'true'),
    'dom.push.serverURL': ('', 'wss://push.services.mozilla.com/'),
}

with open('/app/settings/camoufox.cfg') as f:
    content = f.read()

count = 0
for pref, (old_val, new_val) in fixes.items():
    if old_val in ('true', 'false'):
        old_str = f'defaultPref(\"{pref}\", {old_val})'
        new_str = f'defaultPref(\"{pref}\", {new_val})'
    elif old_val == '':
        old_str = f'defaultPref(\"{pref}\", \"\")'
        new_str = f'defaultPref(\"{pref}\", \"{new_val}\")'
    else:
        old_str = f'defaultPref(\"{pref}\", {old_val})'
        new_str = f'defaultPref(\"{pref}\", {new_val})'
    if old_str in content:
        content = content.replace(old_str, new_str)
        count += 1
    else:
        old_str2 = old_str.replace('defaultPref(', 'pref(')
        new_str2 = new_str.replace('defaultPref(', 'pref(')
        if old_str2 in content:
            content = content.replace(old_str2, new_str2)
            count += 1

with open('/app/settings/camoufox.cfg', 'w') as f:
    f.write(content)
print(f'Total fixed: {count}/{len(fixes)}')
PYEOF"
```

Again, `docker cp` at event #1009 brings the fixed cfg back to the host repo.

---

## A7. `js/src/vm/DateTime.cpp` — ADD the `JS::SetTimeZoneOverride` function body

**Reason**: The Mar 27 doc (section 6.3) used `sed` to replace a `setenv("TZ", timezoneId, 1)`
call with `_putenv_s("TZ", timezoneId)` — but that `sed` only works if the function body
already exists. The Mar 27 session was the one that *added* `extern bool JS::SetTimeZoneOverride`
declarations to `js/public/Date.h` (see doc section 3.2), but **it never added the actual
function body to `DateTime.cpp`**. Mar 29 event #780 added the body; event #789 rewrote it
to use `_putenv_s` + `ResetTimeZoneInternal` instead of a custom `updateDefaultTimeZoneOverride`
path that didn't exist in FF149.

Final inserted body (must be added immediately before `JS_PUBLIC_API bool JS::SetRealmTimeZoneOverride`):

```cpp
JS_PUBLIC_API bool JS::SetTimeZoneOverride(const char* timeZoneId) {
  if (!timeZoneId) {
    return false;
  }
  _putenv_s("TZ", timeZoneId);
  js::ResetTimeZoneInternal(js::ResetTimeZoneMode::ResetEvenIfOffsetUnchanged);
  return true;
}
```

Insertion sed-style recipe (executed as python3 heredoc in event #780, then refined in #789):

```python
old = '''JS_PUBLIC_API void JS::ResetTimeZone() {
  js::ResetTimeZoneInternal(js::ResetTimeZoneMode::ResetEvenIfOffsetUnchanged);
}

JS_PUBLIC_API bool JS::SetRealmTimeZoneOverride'''

new = '''JS_PUBLIC_API void JS::ResetTimeZone() {
  js::ResetTimeZoneInternal(js::ResetTimeZoneMode::ResetEvenIfOffsetUnchanged);
}

JS_PUBLIC_API bool JS::SetTimeZoneOverride(const char* timeZoneId) {
  if (!timeZoneId) {
    return false;
  }
  _putenv_s("TZ", timeZoneId);
  js::ResetTimeZoneInternal(js::ResetTimeZoneMode::ResetEvenIfOffsetUnchanged);
  return true;
}

JS_PUBLIC_API bool JS::SetRealmTimeZoneOverride'''
```

**Note**: This supersedes and completes Mar 27 doc section 6.3. The `sed` in 6.3 should
only be run as a *verification* step (or skipped entirely) on a fresh rebuild — on a clean
checkout there is no `setenv("TZ", timezoneId, 1)` string to match, because the Mar 27
`js/public/Date.h` decl + this new body are the complete patch.

### A7.1 (intermediate attempt, superseded) — `MakeRefPtr<JS::TimeZoneString>`

Event #343 first attempted:

```python
content = content.replace(
    'timeZoneOverride_ = timezone ? std::string(timezone) : std::string();',
    'timeZoneOverride_ = timezone ? MakeRefPtr<JS::TimeZoneString>(timezone) : nullptr;'
)
```

Event #656 corrected to:

```python
content = content.replace(
    'MakeRefPtr<JS::TimeZoneString>(timezone)',
    'RefPtr<JS::TimeZoneString>(new JS::TimeZoneString(timezone))'
)
```

This combination matches Mar 27 doc section 6.2 BUT with the newer `RefPtr<...>(new T(...))`
form. Use this form, not the `MakeRefPtr` one.

---

## A8. `dom/base/nsGlobalWindowInner.h` — move `SetWebGLVendor/Renderer` protected → public

**Reason**: The Mar 27 doc Priority-2 list (line 1126-1128) flagged this as "not found in the
Mar 27 transcript". Mar 29 event #532 did the fix. The webgl-spoofing patch left both setters
in a `protected:` block but they need to be callable from `dom/webidl/Window.webidl` bindings,
which only see `public:` members.

In-container fix (aab7362b event #532):

```python
with open('/app/camoufox-149.0-beta.1/dom/base/nsGlobalWindowInner.h') as f:
    content = f.read()

old_block = '''  // Per-context WebGL parameter spoofing
  void SetWebGLVendor(const nsAString& vendor, mozilla::ErrorResult& aRv);
  void SetWebGLRenderer(const nsAString& renderer, mozilla::ErrorResult& aRv);

 public:'''

new_block = ''' public:
  // Per-context WebGL parameter spoofing
  void SetWebGLVendor(const nsAString& vendor, mozilla::ErrorResult& aRv);
  void SetWebGLRenderer(const nsAString& renderer, mozilla::ErrorResult& aRv);
'''

content = content.replace(old_block, new_block)

# Also add SetAudioFingerprintSeed declaration near SetFontSpacingSeed
content = content.replace(
    '  // Font spacing seed for privacy-preserving font fingerprinting\n  void SetFontSpacingSeed(uint32_t seed, mozilla::ErrorResult& aRv);',
    '  // Audio fingerprint seed for privacy-preserving audio fingerprinting\n  void SetAudioFingerprintSeed(uint32_t seed, mozilla::ErrorResult& aRv);\n\n  // Font spacing seed for privacy-preserving font fingerprinting\n  void SetFontSpacingSeed(uint32_t seed, mozilla::ErrorResult& aRv);'
)

with open('/app/camoufox-149.0-beta.1/dom/base/nsGlobalWindowInner.h', 'w') as f:
    f.write(content)
```

**Note on `SetAudioFingerprintSeed`**: Mar 27 doc section "Re-apply recipe" step 2 already adds
this decl via a different `sed` pattern (after `GetPaintWorklet`). The Mar 29 edit adds it in
a different location (before `SetFontSpacingSeed`). Pick ONE; on a fresh checkout the Mar 27
sed recipe is simpler. This entry is documented only so that if the Mar 29-built binary is
examined, the location discrepancy is explained.

---

## A9. `dom/base/nsGlobalWindowInner.h` + `nsGlobalWindowInner.cpp` + `dom/webidl/Window.webidl` + `dom/base/moz.build` — dedupe WebGL spoofing hunks

**Reason**: The `webgl-spoofing.patch` contains the same diff hunks three times each for
`Window.webidl`, `moz.build`, and `nsGlobalWindowInner.h/cpp`. When applied to FF149 the
same block ends up inserted multiple times. The Mar 27 doc does not mention any dedupe.

### A9.1 `dom/base/moz.build` — keep only first `WebGLParamsManager` block

aab7362b event #405:

```python
block = '''
# WebGLParamsManager compiled separately to avoid unified build scope issues
# (includes RoverfoxStorageManager.h which can affect compilation units)
SOURCES += ["WebGLParamsManager.cpp"]

EXPORTS.mozilla.dom += [
    "WebGLParamsManager.h",
]'''

# Keep only the first occurrence (there were 3)
```

### A9.2 `dom/webidl/Window.webidl` — dedupe vendor + renderer partial interfaces

aab7362b event #429:

```python
vendor_block = '''// Per-context WebGL vendor spoofing
partial interface Window {
  [Throws, Func="mozilla::dom::WebGLParamsManager::IsVendorFunctionEnabledForWebIDL"]
  undefined setWebGLVendor(DOMString vendor);
};'''

renderer_block = '''// Per-context WebGL renderer spoofing
partial interface Window {
  [Throws, Func="mozilla::dom::WebGLParamsManager::IsRendererFunctionEnabledForWebIDL"]
  undefined setWebGLRenderer(DOMString renderer);
};'''

# Keep only the first occurrence of each.
```

### A9.3 `dom/base/nsGlobalWindowInner.h` — dedupe decl lines

aab7362b event #437 (first half):

```python
with open('/app/camoufox-149.0-beta.1/dom/base/nsGlobalWindowInner.h') as f:
    lines = f.readlines()
seen = set()
new_lines = []
for line in lines:
    stripped = line.strip()
    if 'SetWebGLVendor' in stripped or 'SetWebGLRenderer' in stripped:
        if stripped in seen:
            continue
        seen.add(stripped)
    new_lines.append(line)
with open('/app/camoufox-149.0-beta.1/dom/base/nsGlobalWindowInner.h', 'w') as f:
    f.writelines(new_lines)
```

### A9.4 `dom/base/nsGlobalWindowInner.cpp` — dedupe `SetWebGLVendor/Renderer` implementations

Same event #437 (second half): removes duplicate function bodies matching exact text of
`nsGlobalWindowInner::SetWebGLVendor` and `nsGlobalWindowInner::SetWebGLRenderer`. The exact
function bodies that were triplicated:

```cpp
void nsGlobalWindowInner::SetWebGLVendor(const nsAString& vendor,
                                          ErrorResult& aRv) {
  uint32_t userContextId = 0;
  if (BrowsingContext* bc = GetBrowsingContext()) {
    userContextId = bc->OriginAttributesRef().mUserContextId;
  }
  WebGLParamsManager::SetVendor(userContextId, vendor);
  if (userContextId != 0) {
    WebGLParamsManager::SetVendor(0, vendor);
  }
  WebGLParamsManager::DisableVendorFunction(userContextId);
  if (JSContext* cx = nsContentUtils::GetCurrentJSContext()) {
    JS::Rooted<JSObject*> global(cx, JS::CurrentGlobalOrNull(cx));
    if (global) {
      JS::Rooted<JS::Value> undef(cx, JS::UndefinedValue());
      JS_SetProperty(cx, global, "setWebGLVendor", undef);
      JS_DeleteProperty(cx, global, "setWebGLVendor");
    }
  }
}
```

(and the analogous `SetWebGLRenderer` body with `Renderer` substituted). Keep the first
occurrence, delete the 2nd and 3rd.

---

## A10. `dom/workers/WorkerPrivate.h` — add `GetDocument()` declaration

**Reason**: Mar 27 doc Priority-2 list (line 1129) flagged this as NOT FOUND.
aab7362b event #572 added it.

```python
content = content.replace(
    'nsPIDOMWindowInner* GetAncestorWindow() const;',
    'nsPIDOMWindowInner* GetAncestorWindow() const;\n  Document* GetDocument() const;'
)
```

---

## A11. `dom/canvas/OffscreenCanvas.h` — add `GetDocument()` declaration

**Reason**: Mar 27 doc Priority-2 list (line 1130) flagged this as NOT FOUND. Note that the
actual file path is `dom/canvas/OffscreenCanvas.h`, NOT `dom/html/OffscreenCanvas.h` as the
Mar 27 doc wrote (the Mar 27 path was a typo). aab7362b event #588 added it:

```python
content = content.replace(
    'FONT_VISIBILITY_PROVIDER_IMPL',
    'FONT_VISIBILITY_PROVIDER_IMPL\n\n  mozilla::dom::Document* GetDocument() const;'
)
```

---

## A12. `gfx/thebes/gfxTextRun.h` — add `uint32_t aUserContextId = 0` params to 5 methods + `mUserContextId` member + `GetUserContextId()` override

**Reason**: The anti-font-fingerprinting patch's changes to `gfxTextRun.h` silently fail
(the patch claims success but `.rej` files are empty) because the FF149 signatures differ.
Mar 29 events #629, #721, #724, #734 added everything manually.

Complete set of edits (aab7362b event #629 + #721, consolidated):

### A12.1 Add `GetUserContextId` override (public)

```cpp
// BEFORE
gfxFontGroup* GetFontGroup() const { return mFontGroup; }
// AFTER
gfxFontGroup* GetFontGroup() const { return mFontGroup; }
uint32_t GetUserContextId() const override { return mUserContextId; }
```

### A12.2 Add `aUserContextId` param to `gfxTextRun::Create`

```cpp
// BEFORE
static already_AddRefed<gfxTextRun> Create(
    const gfxTextRunFactory::Parameters* aParams, uint32_t aLength,
    gfxFontGroup* aFontGroup, mozilla::gfx::ShapedTextFlags aFlags,
    nsTextFrameUtils::Flags aFlags2);
// AFTER
static already_AddRefed<gfxTextRun> Create(
    const gfxTextRunFactory::Parameters* aParams, uint32_t aLength,
    gfxFontGroup* aFontGroup, mozilla::gfx::ShapedTextFlags aFlags,
    nsTextFrameUtils::Flags aFlags2, uint32_t aUserContextId = 0);
```

### A12.3 Add `aUserContextId` to `gfxTextRun` constructor

```cpp
// BEFORE
gfxTextRun(const gfxTextRunFactory::Parameters* aParams, uint32_t aLength,
           gfxFontGroup* aFontGroup, mozilla::gfx::ShapedTextFlags aFlags,
           nsTextFrameUtils::Flags aFlags2);
// AFTER
gfxTextRun(const gfxTextRunFactory::Parameters* aParams, uint32_t aLength,
           gfxFontGroup* aFontGroup, mozilla::gfx::ShapedTextFlags aFlags,
           nsTextFrameUtils::Flags aFlags2, uint32_t aUserContextId = 0);
```

### A12.4 Add `mUserContextId` member to `gfxTextRun`

```cpp
// BEFORE
bool mDontSkipDrawing;  // true if the text run must not skip drawing
// AFTER
uint32_t mUserContextId;  // user context ID for font spacing seed

bool mDontSkipDrawing;  // true if the text run must not skip drawing
```

### A12.5 Add `aUserContextId` to `gfxFontGroup::MakeTextRun` (Parameters* template overload)

```cpp
// BEFORE
template <typename T>
already_AddRefed<gfxTextRun> MakeTextRun(const T* aString, uint32_t aLength,
                                         const Parameters* aParams,
                                         mozilla::gfx::ShapedTextFlags aFlags,
                                         nsTextFrameUtils::Flags aFlags2,
                                         gfxMissingFontRecorder* aMFR);
// AFTER: append `, uint32_t aUserContextId = 0` before the closing `);`
```

### A12.6 Add `aUserContextId` to `gfxFontGroup::MakeTextRun` (DrawTarget* template overload)

Add the same param and forward it in the inlined body:

```cpp
// BEFORE (body tail)
return MakeTextRun(aString, aLength, &params, aFlags, aFlags2, aMFR);
// AFTER
return MakeTextRun(aString, aLength, &params, aFlags, aFlags2, aMFR, aUserContextId);
```

### A12.7 Add `aUserContextId` to `gfxFontGroup::MakeEmptyTextRun` (event #721)

```cpp
// BEFORE: `(..., nsTextFrameUtils::Flags aFlags2);`
// AFTER:  `(..., nsTextFrameUtils::Flags aFlags2, uint32_t aUserContextId = 0);`
```

### A12.8 Add `aUserContextId` to `gfxFontGroup::MakeSpaceTextRun` (event #721)

Same pattern as A12.7.

### A12.9 Add `aUserContextId` to `gfxFontGroup::MakeBlankTextRun` (event #721)

Same pattern as A12.7, but for the template method.

### A12.10 Add `mUserContextId` member to `gfxFontGroup` (after `mFontListGeneration`)

aab7362b event #721:

```cpp
// BEFORE
uint32_t mFontListGeneration = 0;  // platform font list generation for this
                                   // fontgroup
// AFTER
uint32_t mFontListGeneration = 0;  // platform font list generation for this
                                   // fontgroup

uint32_t mUserContextId = 0;  // user context ID for font spacing seed
```

Event #734 inserted a second copy of this same field by line-index heuristic
(`if 'fontgroup' in line and i > 1400`) — this is a BUG in the Mar 29 run that inserted
the field twice. On a fresh rebuild, apply event #721's replace ONLY and skip event #734.

---

## A13. `gfx/thebes/gfxTextRun.cpp` — add `aUserContextId` param to `MakeBlankTextRun` definition

aab7362b event #724:

```python
c = c.replace(
    'already_AddRefed<gfxTextRun> gfxFontGroup::MakeBlankTextRun(\n    const T* aString, uint32_t aLength, const Parameters* aParams,\n    gfx::ShapedTextFlags aFlags, nsTextFrameUtils::Flags aFlags2) {',
    'already_AddRefed<gfxTextRun> gfxFontGroup::MakeBlankTextRun(\n    const T* aString, uint32_t aLength, const Parameters* aParams,\n    gfx::ShapedTextFlags aFlags, nsTextFrameUtils::Flags aFlags2,\n    uint32_t aUserContextId) {'
)
```

---

## A14. `dom/base/Navigator.cpp` — `#include "js/public/Date.h"` → `"js/Date.h"`

**Reason**: Mar 27 doc section 11.3 already says `#include "js/Date.h"` should be added, but
the anti-font-fingerprinting or navigator-spoofing patch added the wrong path (`js/public/Date.h`
which does not exist in FF149 — the public headers live directly under `js/`). Mar 29
event #700 corrected it:

```python
c = c.replace('#include "js/public/Date.h"', '#include "js/Date.h"')
```

---

## A15. Four files — `MaskConfig::GetBool(` → `MaskConfig::CheckBool(`

**Reason**: `MaskConfig::GetBool(...)` in FF149 returns `std::optional<bool>`, so direct use
in `if (MaskConfig::GetBool("key"))` produces a warning/error (the optional is always truthy).
FF149's `MaskConfig` has a sibling `CheckBool(...)` that returns a plain `bool`. The Mar 27
session applied a different workaround (`.value_or(false)`) on `Http2Session.cpp` (see Mar 27
doc section 9.10). Mar 29 event #358 did a global `GetBool` → `CheckBool` rename on 4 other
files where the same pattern appeared:

```python
files_to_fix = [
    '/app/camoufox-149.0-beta.1/layout/style/GlobalStyleSheetCache.cpp',
    '/app/camoufox-149.0-beta.1/dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp',
    '/app/camoufox-149.0-beta.1/dom/media/MediaDevices.cpp',
    '/app/camoufox-149.0-beta.1/docshell/base/BrowsingContext.cpp',
]
for fpath in files_to_fix:
    content = content.replace('MaskConfig::GetBool(', 'MaskConfig::CheckBool(')
```

**Prerequisite**: `CheckBool` must exist as a static method on `MaskConfig` (in
`additions/camoucfg/MaskConfig.hpp`). If it does not, either add it as a thin wrapper:

```cpp
static bool CheckBool(const char* key) {
    return GetBool(key).value_or(false);
}
```

…or keep the Mar 27 `.value_or(false)` style in these 4 files as well. The Mar 29 session
went with the `CheckBool` rename without verifying `MaskConfig.hpp` — confirm before rebuild.

---

## A16. `js/src/vm/Realm.cpp` — confirmed NO FIX NEEDED in FF149

**Reason**: Mar 27 doc Priority-2 list (line 1134-1135) flagged `js/src/vm/Realm.cpp` and
`Realm.h` as needing the `setTimeZoneOverride` method. Mar 29 event #177 dispatched an Agent
sub-task to fix it, and the agent's result was:

> "Everything is already in place. **No fix needed for `Realm.cpp`.** The patch hunk was
> rejected because it targeted an older codebase (expected Playwright's disabled stub at
> line 545), but Camoufox v149 already ships with the full per-realm timezone implementation
> natively."

**Action for fresh rebuild**: delete `/app/camoufox-149.0-beta.1/js/src/vm/Realm.cpp.rej`
and do not apply the rejected hunks. The native FF149 code is correct.

---

## A17. `dom/canvas/ClientWebGLContext.cpp` — manual re-insertion of hunks 7 and 8 of `webgl-spoofing.patch`

**Reason**: Mar 27 doc Priority-2 list (line 1131-1133) flagged `ClientWebGLContext.cpp` as
not edited in the Mar 27 session. Mar 29 event #178 dispatched an Agent that confirmed
hunks 7 and 8 of `webgl-spoofing.patch` failed because FF149's `UNMASKED_RENDERER_WEBGL`
and `UNMASKED_VENDOR_WEBGL` case blocks were significantly restructured (new
`WebGLRendererConstant`, `WebGLVendorRandomize`, `WebGLVendorConstant`, `WebGLVendorSanitize`
RFP targets).

**Agent result summary** (from the Mar 29 transcript, event #181 result payload):

> Inserted Camoufox spoofing code blocks (WebGLParamsManager lookup + MaskConfig fallback) at:
> - **Line 2608**: Before the RENDERER `ShouldResistFingerprinting` logic
> - **Line 2631**: Before the VENDOR `ShouldResistFingerprinting` logic
>
> The spoofing blocks `break` early when a spoofed value is found, preserving the original
> Firefox behavior as a fallback.
>
> The `.h` file reject was already handled (declarations were already present).
>
> Cleaned up `.rej` files.

**IMPORTANT CAVEAT**: the exact text that was inserted is NOT preserved in the Mar 29 main
transcript — the Agent ran as a sub-session with its own event log that was not persisted
to `aab7362b-*.jsonl`. To reconstruct the exact inserted text on a fresh rebuild, you must:

1. Read the rejected hunks from `patches/webgl-spoofing.patch` (search for
   `ClientWebGLContext.cpp` hunks 7 and 8, which target the `UNMASKED_RENDERER_WEBGL` and
   `UNMASKED_VENDOR_WEBGL` case labels).
2. Hand-apply the `+`-lines (the WebGLParamsManager lookup with MaskConfig fallback block)
   immediately before the respective `ShouldResistFingerprinting` call in each case block,
   using a `break` to short-circuit when a spoofed value is returned.
3. Verify with `grep -n 'WebGLParamsManager::GetVendor\|WebGLParamsManager::GetRenderer'
   dom/canvas/ClientWebGLContext.cpp` — should find the two new call sites near lines
   2608 / 2631.

Alternatively, copy the fixed file out of the existing v149 binary's build tree (which was
pushed to `gh release v149.0-beta.1` on Mar 29 — the build tree in the `cfox` container at
`/app/camoufox-149.0-beta.1/dom/canvas/ClientWebGLContext.cpp` is the authoritative source).

---

## A18. `toolkit/rust/third_party/rust/*/Cargo.toml.orig` — restore from `firefox-149.0` source tree

**Reason**: Cargo's vendored-crate verification refuses to build if any third-party crate is
missing its `Cargo.toml.orig` sidecar (this is cargo's integrity check). Several crates in
the FF149 source tarball were missing these sidecars. aab7362b events #469 and #488 restore
them from the sibling `firefox-149.0/` directory that was used as the unpatched reference:

```bash
# One-off manual restore for the euclid crate (the one that first hit the error)
docker exec cfox bash -c "cp /app/camoufox-149.0-beta.1/third_party/rust/euclid/Cargo.toml \
                            /app/camoufox-149.0-beta.1/third_party/rust/euclid/Cargo.toml.orig"

# Bulk restore of all missing sidecars
docker exec cfox bash -c "cd /app && cp -r firefox-149.0/third_party/rust/*/Cargo.toml.orig /tmp/ 2>/dev/null; \
    for f in firefox-149.0/third_party/rust/*/Cargo.toml.orig; do \
        dest=\"camoufox-149.0-beta.1/\${f#firefox-149.0/}\"; \
        cp \"\$f\" \"\$dest\" 2>/dev/null; \
    done && \
    find camoufox-149.0-beta.1/third_party/rust -name 'Cargo.toml.orig' | wc -l"
```

**Note for fresh rebuild**: you MUST have the unpatched `firefox-149.0/` source tree
side-by-side with `camoufox-149.0-beta.1/` inside the `cfox` container at `/app/` for this
to work. If only `camoufox-149.0-beta.1/` is present, use the euclid-style one-off copy for
each missing crate (the error message will tell you which crate fails first).

---

## A19. `toolkit/components/remote/nsRemoteService.cpp` — disable `SendCommandLine` + `StartupServer` for multi-instance

**Reason**: Discovered in the Mar 27 `06d388b9-*.jsonl` transcript at event #3298 — this is
chronologically Mar 27 but was NOT captured in the existing Mar 27 recovery doc (which only
covered `e19e7540-*.jsonl`). Without this fix, launching two `camoufox.exe` processes causes
one to forward its command line to the other and exit, breaking multi-instance automation.

In-container fix:

```bash
docker exec cfox bash -c "
python3 -c \"
import sys
SRC = '/app/camoufox-149.0-beta.1'
path = f'{SRC}/toolkit/components/remote/nsRemoteService.cpp'
with open(path, 'r') as f:
    src = f.read()

if 'return NS_ERROR_NOT_AVAILABLE;  // Camoufox: multi-instance' in src:
    print('Already patched')
    sys.exit(0)

# Disable SendCommandLine
old = 'nsresult nsRemoteService::SendCommandLine('
assert old in src, 'SendCommandLine not found'
idx = src.index(old)
brace_idx = src.index('{', idx)
src = src[:brace_idx+1] + '\n  return NS_ERROR_NOT_AVAILABLE;  // Camoufox: multi-instance\n' + src[brace_idx+1:]

# Disable StartupServer
old2 = 'void nsRemoteService::StartupServer() {'
assert old2 in src, 'StartupServer not found'
src = src.replace(
    old2,
    'void nsRemoteService::StartupServer() {\n  return;  // Camoufox: multi-instance',
    1
)

with open(path, 'w') as f:
    f.write(src)
print('Remoting disabled: SendCommandLine + StartupServer neutered')
\"
"
```

**Final state**:

```cpp
nsresult nsRemoteService::SendCommandLine(...) {
  return NS_ERROR_NOT_AVAILABLE;  // Camoufox: multi-instance
  // ... original body follows but is unreachable ...
}

void nsRemoteService::StartupServer() {
  return;  // Camoufox: multi-instance
  // ... original body follows but is unreachable ...
}
```

---

## Mar 29 summary — Additional touched files (quick reference)

| # | File | Mar 29 Event(s) | Summary |
| --- | --- | --- | --- |
| A1 | `patches/chromeutil.patch` | 100 | Remove `printf_stderr("DEBUG:...")` line |
| A2 | `patches/librewolf/devtools-bypass.patch` | 240, 243 | `librewolf.*` → `camoufox.*` prefs |
| A3 | `patches/anti-font-fingerprinting.patch` | 1320, 1371 | Remove 2 debug `printf` leaks |
| A4 | `settings/camoufox.cfg` | 117 | Remove `minNumTicks`, `focusmanager.testmode` |
| A5 | `settings/camoufox.cfg` | 117 | `sessionhistory.max_entries` 0 → 50 |
| A6 | `settings/camoufox.cfg` | 942 | 15 prefs aligned to stock Firefox |
| A7 | `js/src/vm/DateTime.cpp` | 780, 789 | ADD `JS::SetTimeZoneOverride` body |
| A8 | `dom/base/nsGlobalWindowInner.h` | 532 | `SetWebGLVendor/Renderer` protected → public; add `SetAudioFingerprintSeed` decl |
| A9.1 | `dom/base/moz.build` | 405 | Dedupe `WebGLParamsManager` block (3 → 1) |
| A9.2 | `dom/webidl/Window.webidl` | 429 | Dedupe `setWebGLVendor/Renderer` partial interfaces |
| A9.3 | `dom/base/nsGlobalWindowInner.h` | 437 | Dedupe `SetWebGLVendor/Renderer` decls |
| A9.4 | `dom/base/nsGlobalWindowInner.cpp` | 437 | Dedupe `SetWebGLVendor/Renderer` impls |
| A10 | `dom/workers/WorkerPrivate.h` | 572 | Add `GetDocument()` decl |
| A11 | `dom/canvas/OffscreenCanvas.h` | 588 | Add `GetDocument()` decl (NOTE: `dom/canvas/`, not `dom/html/`) |
| A12 | `gfx/thebes/gfxTextRun.h` | 629, 721, 734 | 10 edits: add `aUserContextId` params + `mUserContextId` members + `GetUserContextId()` override |
| A13 | `gfx/thebes/gfxTextRun.cpp` | 724 | Add `aUserContextId` to `MakeBlankTextRun` definition |
| A14 | `dom/base/Navigator.cpp` | 700 | `#include "js/public/Date.h"` → `"js/Date.h"` |
| A15 | `layout/style/GlobalStyleSheetCache.cpp` | 358 | `MaskConfig::GetBool` → `CheckBool` |
| A15 | `dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp` | 358 | `MaskConfig::GetBool` → `CheckBool` |
| A15 | `dom/media/MediaDevices.cpp` | 358 | `MaskConfig::GetBool` → `CheckBool` |
| A15 | `docshell/base/BrowsingContext.cpp` | 358 | `MaskConfig::GetBool` → `CheckBool` |
| A16 | `js/src/vm/Realm.cpp` | (agent #177) | **No fix needed**, FF149 has native impl |
| A17 | `dom/canvas/ClientWebGLContext.cpp` | (agent #178) | WebGLParamsManager lookup blocks inserted at ~lines 2608 and 2631 (exact text opaque — reconstruct from patch or binary tree) |
| A18 | `third_party/rust/*/Cargo.toml.orig` | 469, 488 | Restore sidecar files from `firefox-149.0/` sibling tree |
| A19 | `toolkit/components/remote/nsRemoteService.cpp` | (Mar 27 #3298) | Disable `SendCommandLine` + `StartupServer` — multi-instance fix, missed in Mar 27 doc |

---

## Fixes still NOT found (even after checking all 4 Mar 27 + Mar 29 transcripts)

The following items from the original Mar 27 doc Priority-2 list remain unconfirmed as
either "applied" or "not needed". None of the 4 transcripts show a file-modifying tool
call touching them:

- `toolkit/xre/nsXREDirProvider.cpp` — navigator-spoofing reject (event #138 in aab7362b
  listed it alongside Realm.cpp + ClientWebGLContext.cpp as ".rej files present", but no
  follow-up edit/agent call was captured)
- `browser/components/preferences/main.js`
- `browser/app/Makefile.in`
- `additions/juggler/api.js` — only the pythonlib extension's `api.js` was touched
  (Mar 27 doc section 16)

If a fresh rebuild fails because of these, they must be re-derived from scratch.

---

_Generated 2026-04-13 from transcripts_
- `87fce7f2-7c42-42b5-bbac-b68da5a92aa2.jsonl` (5.5 MB, 1394 events, Mar 29 13:07)
- `aab7362b-b1ac-40fb-ad37-d060b6467522.jsonl` (4.0 MB, 1870 events, Mar 29 21:53)
- `06d388b9-bebe-495a-b4e1-b308f06f6c15.jsonl` (10 MB, 3562 events, Mar 27 19:49 — event #3298 only)
- `d1ffeee2-c908-48ff-af74-086ec0a4ea0f.jsonl` (407 KB, 132 events, Mar 27 19:45 — no file-modifying tool calls)

---

# A17 (RESOLVED): `dom/canvas/ClientWebGLContext.cpp` — webgl-spoofing hunks 7 & 8

**Previously listed as "unknown".** The earlier recovery documented an
ephemeral Agent sub-task had applied hunks 7 and 8 in an unrecoverable way.
Upon inspection of `patches/webgl-spoofing.patch` on 2026-04-13, the exact
content of the two failing hunks was found directly in the patch file
(they fail to apply due to context drift between FF146 and FF149, not
because the content is unknown).

## What hunks 7 & 8 do

Both hunks add per-context `WebGLParamsManager::GetRenderer/GetVendor` lookups
AND `MaskConfig::GetString` lookups as the highest-priority source for
`UNMASKED_RENDERER_WEBGL` / `UNMASKED_VENDOR_WEBGL` responses. These are
inserted immediately before the existing `ShouldResistFingerprinting` checks,
so the spoofed values take precedence over Firefox's built-in RFP.

## Patch content (verbatim from `/app/patches/webgl-spoofing.patch` lines 540-575)

```diff
@@ -2441,6 +2585,17 @@
         switch (pname) {
           case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_RENDERER_WEBGL:
+            {
+              nsAutoString stored;
+              if (WebGLParamsManager::GetRenderer(GetUserContextId(), stored)) {
+                ret = Some(NS_ConvertUTF16toUTF8(stored));
+                break;
+              }
+            }
+            if (auto value = MaskConfig::GetString("webGl:renderer")) {
+              ret = Some(value.value());
+              break;
+            }
             if (ShouldResistFingerprinting(RFPTarget::WebGLRenderInfo)) {
               ret = Some("Mozilla"_ns);
             } else {
@@ -2452,6 +2607,17 @@
             break;
           case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_VENDOR_WEBGL:
+            {
+              nsAutoString stored;
+              if (WebGLParamsManager::GetVendor(GetUserContextId(), stored)) {
+                ret = Some(NS_ConvertUTF16toUTF8(stored));
+                break;
+              }
+            }
+            if (auto value = MaskConfig::GetString("webGl:vendor")) {
+              ret = Some(value.value());
+              break;
+            }
             ret = ShouldResistFingerprinting(RFPTarget::WebGLRenderInfo)
                       ? Some("Mozilla"_ns)
                       : GetUnmaskedVendor();
```

## FF149 target location

In the current FF149 source, both targets are inside the third
`GetParameter` override (the one starting at line ~2599 that handles
`std::string` returns). Line numbers:

- `UNMASKED_RENDERER_WEBGL` case: **line 2607** (insert after this case label, before `if (ShouldResistFingerprinting...)`)
- `UNMASKED_VENDOR_WEBGL` case: **line 2619**

## Reason the stock patch fails

FF146 → FF149 drift: the file was substantially refactored. In FF146 the
code at source line 2441 was inside `Get*Parameter*` function `X`. In FF149
the same logic is now inside `GetParameter` function `Y` at line ~2599 with
different surrounding context (added `WebGLVendorRandomize` / `Base64Encode`
block between the renderer and vendor cases). The patch's hunk context no
longer matches.

## Python fix-up script (applies cleanly to FF149)

```python
# python3 fix_a17.py /app/camoufox-149.0-beta.1/dom/canvas/ClientWebGLContext.cpp
import sys, re
p = sys.argv[1]
src = open(p).read()

RENDERER_INSERT = '''          case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_RENDERER_WEBGL:
            {
              nsAutoString stored;
              if (WebGLParamsManager::GetRenderer(GetUserContextId(), stored)) {
                ret = Some(NS_ConvertUTF16toUTF8(stored));
                break;
              }
            }
            if (auto value = MaskConfig::GetString("webGl:renderer")) {
              ret = Some(value.value());
              break;
            }
            if (ShouldResistFingerprinting(RFPTarget::WebGLRenderInfo) ||'''

VENDOR_INSERT = '''          case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_VENDOR_WEBGL:
            {
              nsAutoString stored;
              if (WebGLParamsManager::GetVendor(GetUserContextId(), stored)) {
                ret = Some(NS_ConvertUTF16toUTF8(stored));
                break;
              }
            }
            if (auto value = MaskConfig::GetString("webGl:vendor")) {
              ret = Some(value.value());
              break;
            }
            if (ShouldResistFingerprinting(RFPTarget::WebGLRenderInfo)) {'''

# Match the renderer case block (within the third GetParameter override)
src = re.sub(
    r'          case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_RENDERER_WEBGL:\n'
    r'            if \(ShouldResistFingerprinting\(RFPTarget::WebGLRenderInfo\) \|\|',
    RENDERER_INSERT,
    src,
    count=1,
)

# Match the vendor case block
src = re.sub(
    r'          case dom::WEBGL_debug_renderer_info_Binding::UNMASKED_VENDOR_WEBGL:\n'
    r'            if \(ShouldResistFingerprinting\(RFPTarget::WebGLRenderInfo\)\) \{',
    VENDOR_INSERT,
    src,
    count=1,
)

open(p, 'w').write(src)
print("A17 applied")
```

## Status

**All hunks recovered.** No remaining "unknown" gaps in the webgl-spoofing
patch reconciliation. The other webgl-spoofing hunks (#1-6, #9-12) apply
cleanly via `patch -p1` with fuzz.

---

# Non-critical rejects (safe to ignore on FF149)

Three `.rej` files remain after patch.py runs. All three are safely
ignorable on FF149 — they do NOT block the build and do NOT affect any
feature we use:

## `toolkit/xre/nsXREDirProvider.cpp.rej`

From `patches/librewolf/*` — adds `XRE_MOZ_SYS_NATIVE_MANIFESTS` and
`XRE_MOZ_USER_NATIVE_MANIFESTS` LibreWolf-specific path handlers for
native-messaging manifests. We don't use native messaging, so these
don't matter. **Skip.**

## `browser/components/preferences/main.js.rej`

Removes `alwaysCheckDefault` / `isDefaultPane` / `isNotDefaultPane`
preferences UI entries (the "Set Camoufox as default browser" button).
We never show Firefox preferences UI to a user. **Skip.**

## `browser/app/Makefile.in.rej`

Tries to rename `EXTRA_DEPS += firefox.exe.manifest` to
`camoufox.exe.manifest`. In FF149, the manifest reference has moved
to `browser/app/moz.build` (line 10), so the Makefile.in hunk no longer
matches anything. The build output keeps using `firefox.exe.manifest`
and `firefox.exe`, which are renamed to `camoufox.*` by the post-build
packaging step (see section 17 "Post-build packaging steps"). **Skip.**

---

_All three of these .rej files can be deleted after patch.py runs:_

```bash
cd /app/camoufox-149.0-beta.1
rm -f toolkit/xre/nsXREDirProvider.cpp.rej
rm -f browser/components/preferences/main.js.rej
rm -f browser/app/Makefile.in.rej
```
