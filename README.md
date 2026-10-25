# iNux

An iOS app that sends a Linux app (`.AppImage` or `.flatpak`) to this repository's CI and gets back an unsigned `.ipa` that wraps it.

## How it works

1. In the iNux app, pick a file. The app reads the first bytes to identify the type and CPU architecture.
2. It uploads the file as a temporary release asset (`src-<id>`) and triggers the **Port a Linux app** workflow through the GitHub REST API.
3. On a Linux runner, `Tools/port/port.py` extracts the AppImage (squashfs) or Flatpak bundle (ostree), reads the `.desktop` entry and icon, scans the binaries, and writes a `manifest.json`.
4. On a macOS runner, the generic wrapper in `Template/` is built with the payload embedded as a resource folder, packaged as an unsigned IPA and published as release `app-<id>`. The temporary source release is deleted.
5. The app polls the release, downloads the IPA and offers it through the share sheet. Sign and install it with your usual sideloading tool.

## Setup

- Push this repo to GitHub with Actions enabled. Bumping `VERSION` on `main` builds and releases iNux itself (`build.yml`).
- In the app's Settings, enter the owner and repository name and a GitHub token (classic: `repo` + `workflow`; fine-grained: Contents and Actions read/write on this repository).

## Honest limits

Porting here means packaging plus a compatibility report. The generated app contains the extracted program but **no execution backend**, because iOS cannot run Linux binaries natively: there is no Linux kernel interface, no JIT without special entitlements, and no executing unsigned code from the bundle. `Template/Sources/Runtime.swift` defines an `ExecutionBackend` protocol where an x86-64 emulator or Linux userland layer can be added. Until one is, tapping Launch explains what is missing.

Other limits: Flatpak bundles normally do not include their runtime; type 1 AppImages are unsupported; absolute symlinks are dropped; very large payloads may be rejected by sideloading tools.

Nothing in this repository has been compiled or tested on a device yet.
