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
            # Always restore to a clean unpatched tree, even if `mach clobber` fails.
            run('git clean -fdx; ./mach clobber; git reset --hard unpatched', exit_on_fail=False)
            # Explicitly remove any leftover .rej files (git clean may miss them)
            for root, dirs, files in os.walk('.'):
                for file in files:
                    if file.endswith('.rej'):
                        try:
                            os.remove(os.path.join(root, file))
                        except OSError:
                            pass

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

                # Apply non-roverfox patches first
                for patch_file in non_roverfox:
                    rejects = self._apply_and_check(patch_file)
                    if rejects:
                        failed_patches.append((patch_file, rejects))

                # Apply roverfox patches last
                for patch_file in roverfox:
                    rejects = self._apply_and_check(patch_file)
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
        import time

        print(f"\n*** -> patch -p1 --fuzz=3 -i {patch_file}")
        sys.stdout.flush()

        # Record time before applying so we only detect .rej files from this patch
        start_time = time.time()

        # Apply patch interactively - don't capture stdout/stderr at all
        # This allows prompts to show immediately and user can respond
        # --forward flag: skip patches that appear to be already applied
        # --binary flag: preserve line endings (helps with CRLF vs LF differences)
        # -l flag: ignore whitespace differences
        # --fuzz=3: tolerate up to 3 lines of context mismatch
        result = subprocess.run(
            ['patch', '-p1', '--forward', '-l', '--binary', '--fuzz=3', '-i', patch_file],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True
        )

        # After patch completes, search for any .rej files created during this patch
        raw_rejects = []
        for root, dirs, files in os.walk('.'):
            for file in files:
                if file.endswith('.rej'):
                    reject_path = os.path.join(root, file)
                    if os.path.exists(reject_path):
                        # Only include if created after this patch started.
                        # Subtract 1 second to account for filesystem mtime
                        # resolution (some filesystems round to 1-second).
                        if os.path.getmtime(reject_path) >= start_time - 1:
                            raw_rejects.append(reject_path)

        # Filter out "already applied" false positives:
        # Firefox 149+ ships with many previously-patched changes (e.g. Juggler/Playwright)
        # built-in. When context lines changed but the actual added content is already
        # present in the source, the .rej is a false positive — not a real failure.
        rejects = []
        for rej_path in raw_rejects:
            src_path = rej_path[:-4]  # strip .rej
            if not os.path.exists(src_path):
                rejects.append(rej_path)
                continue
            try:
                with open(rej_path, 'r', errors='replace') as f:
                    rej_content = f.read()
                added_lines = [
                    line[1:].strip()
                    for line in rej_content.split('\n')
                    if line.startswith('+') and not line.startswith('+++') and line[1:].strip()
                ]
                if not added_lines:
                    # Only deletions — already handled by the source change
                    os.remove(rej_path)
                    continue
                with open(src_path, 'r', errors='replace') as f:
                    src_content = f.read()
                found = sum(1 for line in added_lines if line in src_content)
                if found / len(added_lines) >= 0.95:
                    # Content is already present — skip this .rej
                    os.remove(rej_path)
                    continue
            except Exception:
                pass
            rejects.append(rej_path)

        # Clean up remaining .rej files so they don't interfere with subsequent patches
        for rej in rejects:
            if os.path.exists(rej):
                os.remove(rej)

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
