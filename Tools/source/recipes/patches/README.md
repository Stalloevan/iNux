# iOS patches for KDE Frameworks

KDE Frameworks are not officially built for iOS, so some frameworks use APIs
Qt omits there (process spawning, X11/Wayland, D-Bus system services). Each
small fix lives here as a unified diff and is attached to its framework in
`../kde.py` via the module's `inux-patches` list. `Tools/source/build.py`
applies them with `git apply --unidiff-zero -p1` (falling back to `patch`)
after fetching the source, before configuring.

Patches are pinned to the KF version in `kde.py` (`DEFAULT_KF`); the hunk
line numbers must match that tag. When bumping the version, re-check each
patch against the new source.

Current patches:

- `kconfig-ios-qprocess.patch` — guards `kconf_update`'s `QProcess` calls
  with `#if QT_CONFIG(process)`. Qt on iOS has no `QProcess` (no child
  processes); the auto-update step is inert in a single-app sandbox, so the
  calls are compiled out.

## Adding a patch

1. Dispatch a source build and read the first framework compile error.
2. Clone that framework at the pinned KF tag, make the minimal iOS fix, and
   `git diff > Tools/source/recipes/patches/<framework>-ios-<topic>.patch`.
3. Add the file to the framework's `inux-patches` in `kde.py`.
