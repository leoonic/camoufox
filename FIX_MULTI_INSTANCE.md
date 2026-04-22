# Fix: Disable Firefox Remoting Service (multi-instance RDP)

## Problem
2+ Camoufox instances with `--start-debugger-server=PORT` can't run simultaneously.
Process 2 finds Process 1 via `FindWindowW("Mozilla_camoufox_<profile>_RemoteWindow")`,
sends `WM_COPYDATA`, and exits with code 0 (delegation, not crash).

## Root Cause
`nsRemoteService::SendCommandLine()` in nsAppRunner.cpp creates a `nsWinRemoteClient`
that calls `FindWindowW` to find an existing instance's hidden window. If found, it
delegates the command line and exits. `--no-remote` is a no-op since FF131.
`--new-instance` sets `mDisableRemoteClient = true` but does NOT work on Windows.

## Fix: Python script to patch nsAppRunner.cpp

Create `disable_remoting.py` and run it AFTER patches are applied, BEFORE `mach build`.

The fix targets TWO things in `toolkit/xre/nsAppRunner.cpp`:

### 1. Force-skip the remote client (SendCommandLine)

Find the block (around line 5052):
```cpp
if (!mDisableRemoteClient) {
```

Replace with:
```cpp
if (false && !mDisableRemoteClient) {
```

This prevents `SendCommandLine()` from ever running, so no `FindWindowW`, no delegation.

### 2. Skip the remote server (StartupServer)

The server registers the hidden window class. Disabling it too means no window exists
for other instances to find. Find in nsAppRunner.cpp where `StartupServer` is called
(search for `gRemoteService->StartupServer`), and wrap it:

```cpp
// Camoufox: disable remoting server for multi-instance support
// gRemoteService->StartupServer();
```

### Alternative: Patch nsRemoteService.cpp directly

If the nsAppRunner.cpp patterns are hard to match, patch
`toolkit/components/remote/nsRemoteService.cpp` instead:

In `SendCommandLine()` (line ~203), add early return:
```cpp
nsresult nsRemoteService::SendCommandLine(...) {
  return NS_ERROR_NOT_AVAILABLE;  // Camoufox: disable remoting for multi-instance
  // ... rest of original code
}
```

In `StartupServer()` (line ~297), add early return:
```cpp
void nsRemoteService::StartupServer() {
  return;  // Camoufox: disable remoting server for multi-instance
  // ... rest of original code
}
```

## Python script pattern (like apply_fixes.py)

```python
#!/usr/bin/env python3
"""Disable Firefox remoting service for multi-instance RDP support."""

import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/app/camoufox-149.0-beta.1"


def fix_remote_service():
    path = f"{SRC}/toolkit/components/remote/nsRemoteService.cpp"
    with open(path, "r") as f:
        src = f.read()

    # Disable SendCommandLine - add early return
    old = "nsresult nsRemoteService::SendCommandLine("
    assert old in src, "SendCommandLine not found"

    # Find the opening brace of the function
    idx = src.index(old)
    brace_idx = src.index("{", idx)
    # Insert early return right after the opening brace
    src = src[:brace_idx+1] + "\n  return NS_ERROR_NOT_AVAILABLE;  // Camoufox: multi-instance\n" + src[brace_idx+1:]

    # Disable StartupServer - add early return
    old2 = "void nsRemoteService::StartupServer() {"
    assert old2 in src, "StartupServer not found"
    src = src.replace(
        old2,
        "void nsRemoteService::StartupServer() {\n  return;  // Camoufox: multi-instance",
        1
    )

    with open(path, "w") as f:
        f.write(src)
    print("Remoting disabled: SendCommandLine + StartupServer neutered")


if __name__ == "__main__":
    fix_remote_service()
```

## What this does NOT affect
- RDP debug server (`--start-debugger-server=PORT`) -- unrelated, still works
- WebExtension loading -- unrelated
- Profile isolation -- still works
- Any fingerprinting surface -- remoting is invisible to web content

## Verification
After rebuild, launch 2+ instances with different ports and profiles.
Both should stay alive (no exit code 0).
