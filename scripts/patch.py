#!/usr/bin/env python3

"""
The script that patches the Firefox source into the Camoufox source.
Based on LibreWolf's patch script:
https://gitlab.com/librewolf-community/browser/source/-/blob/main/scripts/librewolf-patches.py

Run:
    python3 scripts/init-patch.py <version> <release>
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from _mixin import (
    find_src_dir,
    get_moz_target,
    get_options,
    list_patches,
    patch,
    run,
    temp_cd,
)

options, args = get_options()

"""
Main patcher functions
"""


@dataclass
class Patcher:
    """Patch and prepare the Camoufox source"""

    moz_target: str
    target: str

    def camoufox_patches(self):
        """
        Apply all patches
        """
        version, release = extract_args()
        with temp_cd(find_src_dir('.', version, release)):
            # Reset to unpatched state first (like "Find broken patches")
            print("Resetting to unpatched state...")
            run('git clean -fdx && ./mach clobber && git reset --hard unpatched', exit_on_fail=False)

            # Re-copy additions and settings after reset
            print("Re-copying additions and settings...")
            run(f'bash ../scripts/copy-additions.sh {version} {release}')

            # Create the base mozconfig file
            run('cp -v ../assets/base.mozconfig mozconfig')
            # Set cross building target
            print(f'Using target: {self.moz_target}')
            self._update_mozconfig()

            if not options.mozconfig_only:
                # Apply patches with roverfox patches at the very end
                all_patches = list_patches()
                # Normalize paths and partition into non-roverfox and roverfox
                non_roverfox = []
                roverfox = []
                for p in all_patches:
                    norm = os.path.normpath(p)
                    parts = norm.split(os.sep)
                    if 'roverfox' in parts:
                        roverfox.append(p)
                    else:
                        non_roverfox.append(p)

                # Track patch failures
                failed_patches = []

                # Known-safe rejects: hunks that FAIL on FF149 because the
                # corresponding functionality is added by z-v149-fixup-*.patch
                # files using different anchors, OR is now native in FF149.
                # Key: patch filename (basename). Value: set of .rej file paths
                # (relative to source root) that are safe to ignore.
                KNOWN_SAFE_REJECTS = {
                    'audio-fingerprint-manager.patch': {
                        # z-v149-fixup-01 adds SetAudioFingerprintSeed decl via a
                        # different anchor in nsGlobalWindowInner.h.
                        'dom/base/nsGlobalWindowInner.h.rej',
                    },
                    'screen-spoofing.patch': {
                        # z-v149-fixup-01 handles nsGlobalWindowInner.h decls;
                        # z-v149-fixup-03 handles nsMediaFeatures.cpp.
                        'dom/base/nsGlobalWindowInner.h.rej',
                        'layout/style/nsMediaFeatures.cpp.rej',
                    },
                    'timezone-spoofing.patch': {
                        # z-v149-fixup-02 adds timezone externs to Date.h;
                        # Realm.cpp: FF149 ships native implementation (A16).
                        'js/src/vm/Realm.cpp.rej',
                        'js/public/Date.h.rej',
                    },
                    'webgl-spoofing.patch': {
                        # z-v149-fixup-07 adds UNMASKED_RENDERER/VENDOR inserts.
                        'dom/canvas/ClientWebGLContext.cpp.rej',
                    },
                    'geolocation-spoofing.patch': {
                        # z-v149-fixup-04 handles NetworkGeolocationProvider.
                        'dom/system/NetworkGeolocationProvider.sys.mjs.rej',
                    },
                    'hide-default-browser.patch': {
                        # Non-critical: removes "set as default" UI we don't use.
                        'browser/components/preferences/main.js.rej',
                    },
                    'mozilla_dirs.patch': {
                        # Non-critical: LibreWolf-specific native manifest paths
                        # (XRE_MOZ_SYS_NATIVE_MANIFESTS). We don't use native
                        # messaging so these path handlers don't matter.
                        'toolkit/xre/nsXREDirProvider.cpp.rej',
                    },
                    'windows-theming-bug-modified.patch': {
                        # Non-critical: FF149 moved manifest ref from Makefile.in
                        # to browser/app/moz.build; post-build rename handles it.
                        'browser/app/Makefile.in.rej',
                    },
                    'h2-fingerprint-spoofing.patch': {
                        # z-v149-fixup-06 adds SETTINGS_TYPE_MAX_HEADER_LIST_SIZE
                        # via a different anchor in Http2Session.h.
                        'netwerk/protocol/http/Http2Session.h.rej',
                    },
                }

                def _filter_known_safe(patch_file, rejects):
                    """Remove .rej files that are in KNOWN_SAFE_REJECTS for this
                    patch. Returns the filtered list of rejects that are still
                    considered failures."""
                    basename = os.path.basename(patch_file)
                    safe = KNOWN_SAFE_REJECTS.get(basename, set())
                    if not safe:
                        return rejects
                    filtered = []
                    for rej in rejects:
                        # Normalize: strip leading "./" and use forward slashes
                        norm = rej.lstrip('./').replace(os.sep, '/')
                        if norm in safe:
                            print(f'  [ignored known-safe reject: {norm}]')
                        else:
                            filtered.append(rej)
                    return filtered

                # Apply non-roverfox patches first
                for patch_file in non_roverfox:
                    rejects = self._apply_and_check(patch_file)
                    rejects = _filter_known_safe(patch_file, rejects)
                    if rejects:
                        failed_patches.append((patch_file, rejects))

                # Apply roverfox patches last
                for patch_file in roverfox:
                    rejects = self._apply_and_check(patch_file)
                    rejects = _filter_known_safe(patch_file, rejects)
                    if rejects:
                        failed_patches.append((patch_file, rejects))

                # Report failures
                if failed_patches:
                    print('\n' + '='*70)
                    print(f'ERROR: {len(failed_patches)} patch(es) failed to apply cleanly:')
                    print('='*70)
                    for patch_file, rejects in failed_patches:
                        print(f'\n{patch_file}:')
                        for reject in rejects:
                            print(f'  - {reject}')
                    print('='*70)
                    sys.exit(1)

            print('Complete!')

    def _apply_and_check(self, patch_file):
        """
        Apply a patch and check for reject files.
        Returns list of reject files if any, empty list otherwise.
        """

        print(f"\n*** -> patch -p1 -i {patch_file}")
        sys.stdout.flush()

        # Delete any stale .rej files BEFORE running this patch so that any
        # .rej files found afterwards are guaranteed to be from this patch.
        # (Using mtime comparison for the same purpose is unreliable on
        # filesystems with 1-second mtime resolution, where a .rej created
        # during the same second as start_time appears "older".)
        for root, dirs, files in os.walk('.'):
            for file in files:
                if file.endswith('.rej'):
                    try:
                        os.remove(os.path.join(root, file))
                    except OSError:
                        pass

        # Apply patch interactively - don't capture stdout/stderr at all
        # This allows prompts to show immediately and user can respond
        # --forward flag: skip patches that appear to be already applied
        # --binary flag: preserve line endings (helps with CRLF vs LF differences)
        # -l flag: ignore whitespace differences
        result = subprocess.run(
            ['patch', '-p1', '--forward', '-l', '--binary', '-i', patch_file],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True
        )

        # Collect all .rej files that exist after the patch (guaranteed to
        # be from this patch since we cleaned before running).
        rejects = []
        for root, dirs, files in os.walk('.'):
            for file in files:
                if file.endswith('.rej'):
                    reject_path = os.path.join(root, file)
                    if os.path.exists(reject_path):
                        rejects.append(reject_path)

        # Clean up .rej files so they don't interfere with subsequent patches
        for rej in rejects:
            try:
                os.remove(rej)
            except OSError:
                pass

        return rejects

    def _update_mozconfig(self):
        """
        Helper for adding additional mozconfig code from assets/<target>.mozconfig
        """
        mozconfig_backup = "mozconfig.backup"
        mozconfig = "mozconfig"
        mozconfig_hash = "mozconfig.hash"

        # Create backup if it doesn't exist
        if not os.path.exists(mozconfig_backup):
            if os.path.exists(mozconfig):
                shutil.copy2(mozconfig, mozconfig_backup)
            else:
                with open(mozconfig_backup, 'w', encoding='utf-8') as f:
                    pass

        # Read backup content
        with open(mozconfig_backup, 'r', encoding='utf-8') as f:
            content = f.read()

        # Add target option
        content += f"\nac_add_options --target={self.moz_target}\n"

        # Add target-specific mozconfig if it exists
        target_mozconfig = os.path.join("..", "assets", f"{self.target}.mozconfig")
        if os.path.exists(target_mozconfig):
            with open(target_mozconfig, 'r', encoding='utf-8') as f:
                content += f.read()

        # Calculate new hash
        new_hash = hashlib.sha256(content.encode()).hexdigest()

        # Update mozconfig
        print(f"-> Updating mozconfig, target is {self.moz_target}")
        with open(mozconfig, 'w', encoding='utf-8') as f:
            f.write(content)
        with open(mozconfig_hash, 'w', encoding='utf-8') as f:
            f.write(new_hash)


def add_rustup(*targets):
    """Add rust targets"""
    for rust_target in targets:
        run(f'~/.cargo/bin/rustup target add "{rust_target}"')


def _update_rustup(target):
    """Add rust targets for the given target"""
    if target == "linux":
        add_rustup("aarch64-unknown-linux-gnu", "i686-unknown-linux-gnu")
    elif target == "windows":
        add_rustup("x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc", "i686-pc-windows-msvc")
    elif target == "macos":
        add_rustup("x86_64-apple-darwin", "aarch64-apple-darwin")


"""
Preparation
"""


def extract_args():
    """Get version and release from args"""
    if len(args) != 2:
        sys.stderr.write('error: please specify version and release of camoufox source')
        sys.exit(1)
    return args[0], args[1]


AVAILABLE_TARGETS = ["linux", "windows", "macos"]
AVAILABLE_ARCHS = ["x86_64", "arm64", "i686"]


def extract_build_target():
    """Get moz_target if passed to BUILD_TARGET environment variable"""

    if os.environ.get('BUILD_TARGET'):
        target, arch = os.environ['BUILD_TARGET'].split(',')
        assert target in AVAILABLE_TARGETS, f"Unsupported target: {target}"
        assert arch in AVAILABLE_ARCHS, f"Unsupported architecture: {arch}"
    else:
        target, arch = "macos", "arm64"
    return target, arch


"""
Launcher
"""

if __name__ == "__main__":
    # Extract args
    VERSION, RELEASE = extract_args()

    TARGET, ARCH = extract_build_target()
    MOZ_TARGET = get_moz_target(TARGET, ARCH)
    _update_rustup(TARGET)

    # Check if the folder exists
    if not os.path.exists(f'camoufox-{VERSION}-{RELEASE}/configure.py'):
        sys.stderr.write('error: folder doesn\'t look like a Firefox folder.')
        sys.exit(1)

    # Apply the patches
    patcher = Patcher(MOZ_TARGET, TARGET)
    patcher.camoufox_patches()

    sys.exit(0)  # ensure 0 exit code
