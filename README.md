# iNux

An iOS app that takes a Linux app and produces an unsigned `.ipa` by
**recompiling it from source for iOS**, using this repository's CI. Qt/KDE
apps and GTK 4 / GNOME apps are supported; both run natively, with no
emulation and no X11.

## How it works

1. In the iNux app, enter a Flathub app id (for example `org.kde.kcalc`) or
   pick an `.AppImage`/`.flatpak` file. A file is only used to read the
   app's id and source location; its Linux binary is not run.
2. The app triggers the **Source build** workflow through the GitHub API.
3. `Tools/source/resolve.py` fetches the app's Flathub manifest (the list of
   modules, their sources and build systems) and writes a build plan.
4. The recipe for the app's toolkit adds what its Flatpak runtime would
   normally provide: for a Qt/KDE app (`recipes/kde.py`) Extra CMake
   Modules and the KDE Frameworks it uses; for a GTK/GNOME app
   (`recipes/gnome.py`) GLib, Cairo, Pango, GTK 4 with the iNux UIKit
   backend, libadwaita, and the Adwaita icons and fonts.
5. `Tools/source/build.py` cross-compiles every module for iOS (device
   arm64), statically, with CMake or Meson (and Qt for iOS from
   `aqtinstall` for Qt apps), staging them into a prefix, then builds the
   app itself.
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
  full speed. This is the self-tested path. iOS 17 or later is required
  (Qt 6.10's minimum).
- **KDE apps**: working end to end for KBreakOut (`org.kde.kbreakout`), which
  needs 22 KDE Frameworks plus libkdegames, OpenAL and libsndfile. The
  pipeline works out each app's framework closure from its own CMake files
  (`recipes/kf_graph.py`), builds KDE's build-time tools for the Mac, and
  cross-compiles everything else statically for iOS. The iOS side relies on
  a small translation layer:
  - `compat/`: a `QProcess` stand-in (Qt for iOS has none; launching a
    program fails cleanly, as it does for any unavailable program) and a
    libintl without catalogs (English UI);
  - patched frameworks: kcoreaddons (Unix helpers built on iOS), kdbusaddons
    (`KDBusService` without D-Bus), kcrash (no crash-reporter process),
    kconfig (no helper programs), plus fixes for upstream static-build bugs
    in kpackage, knewstuff and libkdegames (`recipes/patches/`);
  - a KDocTools stand-in (no help viewer on iOS);
  - `ios/`: an adapter that turns the app's unmodified executable into an
    iOS bundle (Qt's UIKit entry point, static Qt and QML plugins, including
    QML plugins KDE installs without CMake targets), and a bootstrap that
    makes bundled data visible to `QStandardPaths`.
  Other KDE apps go through the same steps; ones that use frameworks not
  yet exercised may need a further patch. Builds are cached per module, so
  a retry continues where the last attempt stopped, and the full log of a
  failed build is attached to the `ci-logs` release.
- **GTK/GNOME apps (GTK 4)**: working end to end for Disk Usage Analyzer
  (`org.gnome.baobab`, Vala + GTK 4 + libadwaita) and the GTK self-test.
  GTK has no iOS backend upstream, so iNux adds one
  (`Tools/source/gtk-uikit`, patched into GTK 4.24):
  - each GTK window or popup is a UIView; GTK renders with Cairo and the
    frames are handed to the view's layer (no OpenGL needed);
  - touches become GTK touch events (GTK's own gestures and kinetic
    scrolling apply), iPad trackpads and mice become pointer and scroll
    events, hardware keyboards become key events;
  - text fields bring up the on-screen keyboard through an iOS input
    method; copy and paste of text use the system pasteboard;
  - an app's main window fills the screen (minus the safe area and the
    keyboard); dialogs are centred over it; menus and popovers close on a
    tap outside them; light/dark mode follows iOS;
  - GTK runs the app's unmodified `main()` on its own thread, while UIKit
    keeps the main thread (`gdkuikitentry.m` is the entry point);
  - libadwaita is patched to take light/dark mode from the backend.
  GSettings are kept in a key file in the app's container; fonts are the
  bundled Adwaita fonts plus the iOS system fonts. GTK 3 apps are not
  supported (they would need a GTK 3 backend too). Apps written in Rust,
  Python or JavaScript, and libraries outside the GNOME core (librsvg,
  GStreamer, WebKit, ...), are not built yet.
  The **GTK self-test** workflow builds a small GTK app for devices; **GTK
  simulator test** runs one in the iOS Simulator and publishes a
  screenshot.
- Everything is unsigned; sign the IPA with your own tool before installing.

## The emulator (kept, switched off)

An earlier approach ran the Linux binary directly with a bundled x86-64
emulator (Blink) and a terminal. That code is preserved under `Emulator/`
with its workflows renamed `emulator-*` and set to manual dispatch only. The
app no longer uses it. It is kept in case a no-source fallback is wanted
later; nothing in the current pipeline depends on it.
