# iNux

An iOS app that takes a Linux app and produces an unsigned `.ipa` by
**recompiling it from source for iOS**, using this repository's CI.

## How it works

1. In the iNux app, enter a Flathub app id (for example `org.kde.kcalc`) or
   pick an `.AppImage`/`.flatpak` file. A file is only used to read the
   app's id and source location; its Linux binary is not run.
2. The app triggers the **Source build** workflow through the GitHub API.
3. `Tools/source/resolve.py` fetches the app's Flathub manifest (the list of
   modules, their sources and build systems) and writes a build plan.
4. For a Qt/KDE app, `Tools/source/recipes/kde.py` adds the runtime
   dependencies that `org.kde.Platform` would normally provide — Extra CMake
   Modules and KDE Frameworks 6 — in dependency order.
5. `Tools/source/build.py` cross-compiles every module for iOS (device
   arm64) with the iOS toolchain and Qt for iOS (installed by `aqtinstall`),
   staging them into a prefix, then builds the app itself.
6. The resulting `.app` is wrapped as an unsigned IPA and published as a
   release. The app downloads it. Sign it with your usual tool to install.

Building iNux itself: bumping `VERSION` on `main` builds and releases the
iNux IPA. On every change to the pipeline, **Source-build self-test**
compiles a small Qt app from source for iOS and publishes it as the
`source-selftest` pre-release, so the core path stays verified.

## Setup

Push this repo to GitHub with Actions enabled. In the app's Settings, enter
the owner and repository name and a GitHub token (classic: `repo` +
`workflow`; fine-grained: Contents and Actions read/write).

## What works, and what is still rough

- **Command-line and pure-Qt apps**: compile cleanly and run natively at
  full speed. This is the self-tested path.
- **KDE apps**: in progress. The pipeline resolves the app, cross-compiles
  Extra CMake Modules, patches ECM's platform check so KDE Frameworks will
  configure for iOS, and compiles the frameworks from source
  (`recipes/kde.py`). KDE does not officially target iOS, so individual
  frameworks use APIs Qt omits there and need small per-framework patches,
  kept under `recipes/patches/` and applied by the build driver. The first
  (kconfig's `QProcess` use) is in place; frameworks like kconfig's
  `kconf_update` helper, kxmlgui and kwindowsystem still need their own
  patches before a full KDE app such as kcalc links. The mechanism and the
  path to add them are documented in `recipes/patches/README.md`. Choose
  which frameworks to build with the workflow's `frameworks` input.
- **GTK/GNOME apps**: not supported yet. GTK has no iOS backend, so the
  stack (glib, GTK) and a UIKit GDK backend still need to be built; this is
  the next piece of work (see the open task in the repo).
- Everything is unsigned; sign the IPA with your own tool before installing.

## The emulator (kept, switched off)

An earlier approach ran the Linux binary directly with a bundled x86-64
emulator (Blink) and a terminal. That code is preserved under `Emulator/`
with its workflows renamed `emulator-*` and set to manual dispatch only. The
app no longer uses it. It is kept in case a no-source fallback is wanted
later; nothing in the current pipeline depends on it.
