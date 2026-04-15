# Rebuild Camoufox v149 from scratch

This document explains how to rebuild the Windows x86_64 Camoufox binary
targeting Firefox 149.0 with all anti-detection patches, starting from a
clean host.

## Prerequisites

- Windows with Docker Desktop (or a Linux machine with Docker)
- ~30 GB free disk space
- ~24 GB RAM available to the builder (WSL2 on Windows)
- This repository cloned locally

## Quick rebuild (existing cfox container)

If the `cfox` container is still alive (check `docker ps -a | grep cfox`), you
can reuse its pre-built toolchain + source tarball. Full rebuild:

```bash
docker start cfox

# Sync any patch changes from the host into the container
for f in ./patches/*.patch ./patches/**/*.patch ./scripts/patch.py ./upstream.sh; do
  docker cp "$f" cfox:/app/$f 2>/dev/null
done

# Clean reset + full rebuild (~60-90 min)
docker exec cfox bash -c '
  cd /app && \
  export BUILD_TARGET=windows,x86_64 && \
  (cd camoufox-149.0-beta.1 && git clean -fdx && git reset --hard unpatched) && \
  python3 scripts/patch.py 149.0 beta.1 && \
  make set-target && \
  make build && \
  make package-windows arch=x86_64
'

# Copy the output ZIP back to host
docker cp cfox:/app/camoufox-149.0-beta.1-win.x86_64.zip ./dist/
```

## Full clean rebuild (no cfox container)

If you need to recreate the container from scratch:

```bash
# 1. Build the Docker image (takes ~45-90 min: apt-get, rustup, make setup,
#    make mozbootstrap). This downloads FF149 source and sets up cross-compile
#    toolchains for Windows.
docker build -t camoufox-builder .

# 2. Start an interactive container named cfox
docker run -it --name cfox --memory=22g --entrypoint bash camoufox-builder

# 3. Inside the container, run the build
cd /app && \
export BUILD_TARGET=windows,x86_64 && \
python3 scripts/patch.py 149.0 beta.1 && \
make set-target && \
make build && \
make package-windows arch=x86_64
```

## Deploying a built binary to Windows

```bat
REM From PowerShell / cmd:
docker cp cfox:/app/camoufox-149.0-beta.1-win.x86_64.zip %TEMP%\camoufox-149.zip

set DEST=%LOCALAPPDATA%\camoufox\camoufox\Cache\browsers\leoonic\149.0-beta.1
rmdir /s /q "%DEST%"
mkdir "%DEST%"
7z x -y %TEMP%\camoufox-149.zip -o%DEST%
if exist %DEST%\firefox (
  xcopy /s /y /q %DEST%\firefox\* %DEST%\
  rmdir /s /q %DEST%\firefox
)
copy /y %DEST%\firefox.exe %DEST%\camoufox.exe
echo {"version":"149.0","build":"beta.1","prerelease":false} > %DEST%\version.json
echo {"active_version":"browsers/leoonic/149.0-beta.1"} > %LOCALAPPDATA%\camoufox\camoufox\Cache\config.json
```

## Known-safe rejects

The build produces `.rej` files for a handful of upstream patches that are
EITHER redundant with our `z-v149-fixup-*.patch` files OR target code that
no longer exists in FF149. `scripts/patch.py` filters these via its
`KNOWN_SAFE_REJECTS` map. The list (as of 2026-04-13):

| Patch | Reject file | Reason |
|-------|-------------|--------|
| `audio-fingerprint-manager.patch` | `dom/base/nsGlobalWindowInner.h.rej` | z-v149-fixup-01 covers |
| `screen-spoofing.patch` | `dom/base/nsGlobalWindowInner.h.rej` + `layout/style/nsMediaFeatures.cpp.rej` | z-v149-fixup-01 + -03 cover |
| `timezone-spoofing.patch` | `js/src/vm/Realm.cpp.rej` + `js/public/Date.h.rej` | FF149 native (A16) + z-v149-fixup-02 |
| `webgl-spoofing.patch` | `dom/canvas/ClientWebGLContext.cpp.rej` | z-v149-fixup-07 covers hunks 7-8 |
| `geolocation-spoofing.patch` | `dom/system/NetworkGeolocationProvider.sys.mjs.rej` | z-v149-fixup-04 covers |
| `hide-default-browser.patch` | `browser/components/preferences/main.js.rej` | Non-critical UI |
| `mozilla_dirs.patch` | `toolkit/xre/nsXREDirProvider.cpp.rej` | LibreWolf-specific, non-critical |
| `windows-theming-bug-modified.patch` | `browser/app/Makefile.in.rej` | FF149 moved manifest ref to moz.build |
| `h2-fingerprint-spoofing.patch` | `Http2Session.h.rej` + `Http2Session.cpp.rej` | z-v149-fixup-06 covers |
| `anti-font-fingerprinting.patch` | `layout/mathml/nsMathMLChar.cpp.rej` | Hunk #2 targets FF146 nsPropertiesTable (default param=0 makes the unpatched caller compile fine) |

## Recovery procedure if source state is lost

The Mar 2026 sessions made ~40 manual fixes that were never committed to git
and almost got lost when `git reset --hard unpatched` ran. Everything is now
captured either in:

- `docs/v149_manual_fixes_recovered.md` — 2248-line forensic recovery doc
- `patches/z-v149-fixup-*.patch` — 11 fixup patches
- `scripts/patch.py` `KNOWN_SAFE_REJECTS` map

If you ever lose the cfox container source tree, running `scripts/patch.py`
from a clean FF149 source WILL now reproduce the build-ready state without
manual intervention. `make build` should succeed on the first try.

## Build timing (reference)

- `make setup` (first time, tarball + mozbootstrap): ~45-60 min
- `scripts/patch.py` (apply all patches + fixups): ~3-5 min
- `./mach clobber` (in `git reset --hard unpatched`): instant
- `./mach build` (full rebuild after clobber): ~50-80 min
- `make package-windows`: ~3-5 min
- **Total clean rebuild**: ~60-90 min after the container is warm

Incremental builds (same source tree, small patch change): ~10-30 min.

## Troubleshooting

### OOM during Rust `gkrust` link

Observed in build v6: SIGKILL on rustc when compiling `gkrust` with LTO. WSL2
at 23 GB ran out of peak memory. Fix: increase WSL2 to 32 GB in `.wslconfig`
OR retry with the obj tree still in place (incremental resume skips the
already-compiled crates).

### Patches reject on a hunk that used to work

Almost always caused by:
1. Line drift between FF146 and FF149 (add fuzz with `-l` or update hunk
   start lines)
2. Malformed `@@` headers after manual line removal (see recovery doc
   sections A1, A3 — you MUST update the line counts when removing lines
   from a hunk body)

If a new reject appears, check if it's covered by a `z-v149-fixup-*.patch`
and if so, add the reject path to `KNOWN_SAFE_REJECTS` in `scripts/patch.py`.
