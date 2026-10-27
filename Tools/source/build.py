#!/usr/bin/env python3
"""Cross-compile a resolved build plan for iOS (device arm64).

  build.py --plan PLAN.json --prefix STAGE --qt QT_IOS_DIR [--prepend RECIPE.json]
           [--host-prefix HOST] [--fetch-only] [--jobs N] [--source-cache DIR]
           [--bundle-id ID] [--display-name NAME]

Module flags (from Tools/source/recipes):
  host           build natively for macOS into HOST (build-time tools)
  kde            KDE/Qt module: iOS options, iNux compat (QProcess, libintl)
                 force-included, KF6_HOST_TOOLING pointing at HOST
  app            the final app: Tools/source/ios/InuxIOSApp.cmake turns its
                 executable into an iOS app bundle
  ecm            Extra CMake Modules: after install, its platform check is
                 relaxed to allow iOS (KDE does not list iOS officially)
  inux-patches   diffs applied to the fetched source
  inux-app-link  libraries the final app must link (third-party deps that
                 projects find with plain find_library)

Each finished module leaves a stamp in its prefix. A rerun skips modules
whose stamp matches, until the first one that has to be rebuilt; from there
everything is rebuilt, since later modules compile against earlier ones.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

# INUX_IOS_SDK=iphonesimulator builds for the iOS Simulator instead of
# devices (used by CI to launch apps and take screenshots).
SDK = os.environ.get("INUX_IOS_SDK", "iphoneos")
SIMULATOR = SDK == "iphonesimulator"
DEPLOYMENT = "17.0"  # Qt 6.10 for iOS is built for iOS 17
HERE = os.path.dirname(os.path.abspath(__file__))
STAMP_VERSION = "3"
# Bumped when iOS build settings in this file change in ways that make
# earlier iOS builds wrong (e.g. host libraries leaking in); host tools stay
# cached.
IOS_ENV_VERSION = "2"


def log(*a):
    print(*a, flush=True)


def run(cmd, cwd=None, env=None, check=True):
    shown = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)
    log("+ (%s) %s" % (cwd or ".", shown))
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str))
    if check and r.returncode != 0:
        raise SystemExit("command failed (%d): %s" % (r.returncode, shown))
    return r.returncode


def xcrun(*args):
    return subprocess.check_output(["xcrun", "--sdk", SDK, *args], text=True).strip()


class Toolchain:
    def __init__(self, prefix, qt, host_prefix):
        self.prefix = prefix
        self.host_prefix = host_prefix
        self.qt = qt
        self.sysroot = xcrun("--show-sdk-path")
        self.cc = xcrun("--find", "clang")
        self.cxx = xcrun("--find", "clang++")
        self.arch_flags = "-arch arm64 -isysroot %s -m%s-version-min=%s" % (
            self.sysroot, "ios-simulator" if SIMULATOR else "iphoneos", DEPLOYMENT)
        # Extra host prefixes (e.g. Homebrew's gettext for host ki18n).
        self.host_extra = [p for p in os.environ.get("INUX_HOST_PREFIX_PATH", "").split(":") if p]
        for p in (prefix, host_prefix):
            for d in ("bin", "lib", "include", "lib/pkgconfig", "share"):
                os.makedirs(os.path.join(p, d), exist_ok=True)
        self.sim_compat = ""
        if SIMULATOR:
            # The simulator's libSystem exports a few functions its headers
            # do not declare, so link-based configure checks find them and
            # the code then fails to compile. Declare them.
            self.sim_compat = os.path.join(prefix, "include", "inux-simulator-compat.h")
            with open(self.sim_compat, "w") as f:
                f.write("/* iNux: declarations missing from the iOS Simulator SDK headers. */\n"
                        "#pragma once\n"
                        "#ifndef __ASSEMBLER__\n"
                        "#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
                        "int pipe2(int fds[2], int flags);\n"
                        "int dup3(int oldfd, int newfd, int flags);\n"
                        "#ifdef __cplusplus\n}\n#endif\n"
                        "#endif\n")

    def qt_host(self):
        # aqt --autodesktop installs the host Qt next to the iOS one.
        host = os.path.join(os.path.dirname(self.qt), "macos")
        return host if os.path.isdir(host) else self.qt

    def env(self, host=False):
        e = dict(os.environ)
        if host:
            e.pop("CFLAGS", None)
            e.pop("CXXFLAGS", None)
            e.pop("LDFLAGS", None)
            prefixes = [self.host_prefix] + self.host_extra + ([self.qt_host()] if self.qt else [])
            e["CMAKE_PREFIX_PATH"] = ":".join(prefixes)
            e["PKG_CONFIG_PATH"] = ":".join("%s/lib/pkgconfig" % p for p in prefixes)
            path = [os.path.join(self.host_prefix, "bin")] + [os.path.join(p, "bin") for p in self.host_extra]
            if self.qt:
                path.append(os.path.join(self.qt_host(), "bin"))
        else:
            cflags = "%s -fPIC -I%s/include" % (self.arch_flags, self.prefix)
            ldflags = "%s -L%s/lib" % (self.arch_flags, self.prefix)
            e.update(CC=self.cc, CXX=self.cxx, CFLAGS=cflags, CXXFLAGS=cflags, LDFLAGS=ldflags)
            e["PKG_CONFIG_PATH"] = "%s/lib/pkgconfig:%s/share/pkgconfig" % (self.prefix, self.prefix)
            e["PKG_CONFIG_LIBDIR"] = e["PKG_CONFIG_PATH"]
            e["CMAKE_PREFIX_PATH"] = ":".join([self.prefix] + ([self.qt] if self.qt else []))
            # Host tools first (msgfmt, kconfig_compiler); never the iOS Qt's bin.
            path = [os.path.join(self.host_prefix, "bin")] + [os.path.join(p, "bin") for p in self.host_extra]
            if self.qt:
                path.append(os.path.join(self.qt_host(), "bin"))
        e["PATH"] = ":".join(path) + ":" + e["PATH"]
        return e

    def cmake_args(self, module):
        host = is_host(module)
        if host:
            prefixes = [self.host_prefix] + self.host_extra + ([self.qt_host()] if self.qt else [])
            return [
                "-DCMAKE_INSTALL_PREFIX=" + self.host_prefix,
                "-DCMAKE_PREFIX_PATH=" + ";".join(prefixes),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_TESTING=OFF",
                "-DBUILD_SHARED_LIBS=OFF",
                # Older projects declare cmake_minimum_required < 3.5, which
                # CMake 4 rejects unless told the minimum policy to assume.
                "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            ]
        prefixes = [self.prefix] + ([self.qt] if self.qt else [])
        args = [
            "-DCMAKE_TOOLCHAIN_FILE=" + os.path.join(HERE, "ios.toolchain.cmake"),
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=" + DEPLOYMENT,
            "-DCMAKE_OSX_SYSROOT=" + self.sysroot,
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=" + self.prefix,
            "-DCMAKE_PREFIX_PATH=" + ";".join(prefixes),
            "-DCMAKE_FIND_ROOT_PATH=" + ";".join(prefixes),
            "-DBUILD_SHARED_LIBS=OFF",
            "-DBUILD_TESTING=OFF",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            # Never pick up macOS libraries (Homebrew, the runner's Python
            # toolcache) for an iOS build.
            "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local;/Users/runner/hostedtoolcache",
        ]
        if self.qt:
            args.append("-DQT_HOST_PATH=" + self.qt_host())
        # Host programs iOS builds need, given by path because host prefixes
        # are excluded from searches (KF6I18n requires gettext's msgfmt).
        for var, prog in (("GETTEXT_MSGFMT_EXECUTABLE", "msgfmt"), ("GETTEXT_MSGMERGE_EXECUTABLE", "msgmerge")):
            for p in self.host_extra:
                exe = os.path.join(p, "bin", prog)
                if os.path.exists(exe):
                    args.append("-D%s=%s" % (var, exe))
                    break
        if module.get("kde"):
            # Searched before Qt's headers (which come in as -isystem /
            # -iframework), so <QProcess> resolves to the iNux stand-in.
            override_dir = os.path.join(self.prefix, "include", "inux-compat-override")
            compat_libs = [os.path.join(self.prefix, "lib", n) for n in ("libInuxCompat.a", "libintl.a")]
            if not module.get("app"):
                # Tools inside library builds need an entry point (see
                # compat-toolentry); the app uses Qt's real one.
                compat_libs.append(os.path.join(self.prefix, "lib", "libInuxToolEntry.a"))
            args += [
                "-DKF6_HOST_TOOLING=" + os.path.join(self.host_prefix, "lib", "cmake"),
                "-DCMAKE_CXX_FLAGS=%s -I%s" % (self.arch_flags, override_dir),
                "-DCMAKE_CXX_STANDARD_LIBRARIES=" + " ".join(compat_libs),
                # KDE installs app bundles to /Applications/KDE by default.
                "-DKDE_INSTALL_BUNDLEDIR=" + os.path.join(self.prefix, "Applications"),
            ]
        return args


def is_host(module):
    return bool(module.get("host") or module.get("build-options", {}).get("host"))


# ------------------------------------------------------- fetching sources --


def fetch_sources(module, srcdir, cache, plan_dir):
    if os.path.isdir(srcdir):
        shutil.rmtree(srcdir)
    os.makedirs(srcdir)
    for s in module.get("sources", []):
        t = s.get("type")
        if t == "git":
            cmd = ["git", "clone", "-q", "--recurse-submodules", "--shallow-submodules"]
            if s.get("tag") or s.get("branch"):
                cmd += ["--depth", "1", "--branch", s.get("tag") or s.get("branch")]
            run(cmd + [s["url"], srcdir + ".git-tmp"])
            if s.get("commit"):
                run(["git", "fetch", "-q", "--depth", "1", "origin", s["commit"]], cwd=srcdir + ".git-tmp", check=False)
                run(["git", "checkout", "-q", s["commit"]], cwd=srcdir + ".git-tmp", check=False)
            move_contents(srcdir + ".git-tmp", os.path.join(srcdir, s.get("dest", "")))
        elif t == "archive" and s.get("url"):
            fn = download(s["url"], cache, s.get("sha256"))
            extract(fn, os.path.join(srcdir, s.get("dest", "")), strip=s.get("strip-components", 1))
        elif t == "file" and s.get("url"):
            fn = download(s["url"], cache, s.get("sha256"))
            dest = os.path.join(srcdir, s.get("dest", ""))
            os.makedirs(dest, exist_ok=True)
            shutil.copy(fn, os.path.join(dest, s.get("dest-filename") or os.path.basename(s["url"])))
        elif t == "file" and s.get("path"):
            dest = os.path.join(srcdir, s.get("dest", ""))
            os.makedirs(dest, exist_ok=True)
            src = s["path"] if os.path.isabs(s["path"]) else os.path.join(plan_dir, s["path"])
            shutil.copy(src, os.path.join(dest, s.get("dest-filename") or os.path.basename(src)))
        elif t == "dir" and s.get("path"):
            src = s["path"] if os.path.isabs(s["path"]) else os.path.join(plan_dir, s["path"])
            shutil.copytree(src, os.path.join(srcdir, s.get("dest", "")), dirs_exist_ok=True)
        elif t == "patch" and (s.get("path") or s.get("paths")):
            for p in ([s["path"]] if s.get("path") else s["paths"]):
                full = p if os.path.isabs(p) else os.path.join(plan_dir, p)
                apply_patch(full, srcdir, s.get("strip-components", 1), s.get("options", []))
        elif t == "shell":
            for cmd in s.get("commands", []):
                run(cmd, cwd=srcdir, check=False)
    return srcdir


def move_contents(src, dest):
    os.makedirs(dest, exist_ok=True)
    for item in os.listdir(src):
        if item == ".git":
            continue
        target = os.path.join(dest, item)
        if os.path.exists(target):
            shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
        shutil.move(os.path.join(src, item), target)
    shutil.rmtree(src)


def download(url, cache, sha256=None):
    os.makedirs(cache, exist_ok=True)
    name = os.path.join(cache, (sha256 or hashlib.sha1(url.encode()).hexdigest())[:16] + "-" +
                        os.path.basename(url.split("?")[0]))
    if os.path.exists(name):
        return name
    log("downloading", url)
    tmp = name + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "iNux-build"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    if sha256:
        h = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        if h != sha256:
            raise SystemExit("sha256 mismatch for %s: got %s" % (url, h))
    os.rename(tmp, name)
    return name


def extract(path, dest, strip=1):
    tmp = dest.rstrip("/") + ".x"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(path) as t:
            t.extractall(tmp)
    entries = os.listdir(tmp)
    root = os.path.join(tmp, entries[0]) if strip and len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])) else tmp
    move_contents(root, dest)
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)


def apply_patch(path, srcdir, strip=1, options=()):
    # Not "git apply": the sources are unpacked inside this repository's
    # checkout, where git resolves paths against the outer repo and skips
    # every file without an error. patch(1) works on any directory and
    # fails loudly.
    log("applying patch", os.path.basename(path))
    if run(["patch", "-p%d" % strip, "-N", "-i", path], cwd=srcdir, check=False) != 0:
        raise SystemExit("patch %s did not apply in %s" % (path, srcdir))


# ----------------------------------------------------------- build systems --


def detect_buildsystem(srcdir, declared):
    if declared and declared not in ("auto",):
        return declared
    if os.path.exists(os.path.join(srcdir, "CMakeLists.txt")):
        return "cmake-ninja"
    if os.path.exists(os.path.join(srcdir, "meson.build")):
        return "meson"
    if os.path.exists(os.path.join(srcdir, "configure")) or os.path.exists(os.path.join(srcdir, "autogen.sh")):
        return "autotools"
    return "simple"


def module_hash(module):
    if not SIMULATOR:
        # Simulator-only settings do not change device builds.
        module = {k: v for k, v in module.items() if k != "inux-simulator-compat"}
    data = json.dumps(module, sort_keys=True)
    for rel in module.get("inux-patches", []):
        p = os.path.join(HERE, "recipes", rel)
        if os.path.exists(p):
            data += open(p).read()
    for s in module.get("sources", []):
        if s.get("type") == "dir" and s.get("path") and os.path.isdir(s["path"]):
            for root, _d, files in sorted(os.walk(s["path"])):
                for f in sorted(files):
                    data += f + open(os.path.join(root, f), "rb").read().decode("latin-1")
    data += open(os.path.join(HERE, "ios.toolchain.cmake")).read()
    if not is_host(module):
        data += "ios-env-" + IOS_ENV_VERSION
        if SIMULATOR:
            data += "-simulator"
    if module.get("app"):
        for f in ("InuxIOSApp.cmake", "inux_ios_bootstrap.cpp"):
            data += open(os.path.join(HERE, "ios", f)).read()
    return hashlib.sha256((STAMP_VERSION + data).encode("utf-8", "replace")).hexdigest()


def stamp_path(tc, module):
    base = tc.host_prefix if is_host(module) else tc.prefix
    return os.path.join(base, ".inux-stamps", module["name"] + ".stamp")


def build_module(module, tc, srcroot, cache, jobs, plan_dir, app_settings):
    name = module["name"]
    host = is_host(module)
    if not host and module.get("only-arches") and "aarch64" not in module["only-arches"]:
        log("skipping %s (only-arches=%s)" % (name, module["only-arches"]))
        return
    if not host and module.get("skip-arches") and "aarch64" in module["skip-arches"]:
        log("skipping %s (skip-arches)" % name)
        return

    srcdir = os.path.join(srcroot, name)
    primary = fetch_sources(module, srcdir, cache, plan_dir)
    for rel in module.get("inux-patches", []):
        apply_patch(os.path.join(HERE, "recipes", rel), primary)
    if module.get("subdir"):
        primary = os.path.join(primary, module["subdir"])

    bs = detect_buildsystem(primary, module.get("buildsystem"))
    env = tc.env(host=host)
    if not host:
        fix_static_package_configs(tc.prefix, tc.qt)
    if module.get("app"):
        normalize_executable_link_signature(primary)
    opts = [o.replace("@HOST_PREFIX@", tc.host_prefix).replace("@PREFIX@", tc.prefix)
            for o in module.get("config-opts", [])]
    log("=== building %s (%s%s)" % (name, bs, ", host tool" if host else ""))

    if bs in ("cmake-ninja", "cmake"):
        builddir = os.path.join(srcroot, name + "-build")
        if os.path.isdir(builddir):
            shutil.rmtree(builddir)
        os.makedirs(builddir)
        gen = ["-G", "Ninja"] if shutil.which("ninja") else []
        extra = []
        if module.get("app"):
            extra = [
                "-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=" + os.path.join(HERE, "ios", "InuxIOSApp.cmake"),
                "-DINUX_APP_TARGET=" + (module.get("app-target") or ""),
                "-DINUX_BUNDLE_ID=" + app_settings["bundle_id"],
                "-DINUX_DISPLAY_NAME=" + app_settings["display_name"],
                "-DINUX_COMPAT_PREFIX=" + tc.prefix,
                "-DINUX_EXTRA_LINK_LIBS=" + ";".join(app_settings["extra_link"]),
            ]
        run(["cmake", "-S", primary, "-B", builddir] + gen + tc.cmake_args(module) + extra + opts, env=env)
        run(["cmake", "--build", builddir, "-j", str(jobs)], env=env)
        run(["cmake", "--install", builddir], env=env)
        if module.get("app"):
            with open(os.path.join(tc.prefix, ".inux-app-build-dir"), "w") as f:
                f.write(builddir)
    elif bs == "meson":
        install_prefix = tc.host_prefix if host else tc.prefix
        builddir = os.path.join(srcroot, name + "-build")
        if os.path.isdir(builddir):
            shutil.rmtree(builddir)
        setup = ["meson", "setup", builddir, primary,
                 "--prefix", install_prefix, "--libdir", "lib",
                 "--default-library", "static", "--buildtype", "release",
                 "-Db_ndebug=if-release"]
        if not host:
            # Cross builds: the compilers, flags and the iOS pkg-config path
            # come from the cross file. CC/CFLAGS/PKG_CONFIG_* in the
            # environment would apply to Meson's native (build machine)
            # lookups and break them.
            for var in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "OBJC", "OBJCFLAGS",
                        "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "CMAKE_PREFIX_PATH"):
                env.pop(var, None)
            app_link = APP_LINK_ARGS if module.get("app") else []
            setup += ["--cross-file", write_meson_cross(tc, os.path.join(srcroot, name + "-cross.ini"), app_link,
                                                        sim_compat=module.get("inux-simulator-compat", False))]
        run(setup + opts, env=env)
        run(["meson", "compile", "-C", builddir, "-j", str(jobs)], env=env)
        run(["meson", "install", "-C", builddir], env=env)
        if module.get("app"):
            record_app_executable(tc, module, builddir)
    elif bs == "autotools":
        install_prefix = tc.host_prefix if host else tc.prefix
        if not os.path.exists(os.path.join(primary, "configure")) and os.path.exists(os.path.join(primary, "autogen.sh")):
            run(["./autogen.sh"], cwd=primary, env=dict(env, NOCONFIGURE="1"), check=False)
        conf = ["./configure", "--prefix=" + install_prefix, "--enable-static", "--disable-shared"]
        if not host:
            conf += ["--host=aarch64-apple-darwin"]
        run(conf + opts, cwd=primary, env=env)
        run(["make", "-j", str(jobs)] + module.get("make-args", []), cwd=primary, env=env)
        run(["make", "install"], cwd=primary, env=env)
    elif bs == "simple":
        env["FLATPAK_DEST"] = tc.host_prefix if host else tc.prefix
        for cmd in module.get("build-commands", []):
            run(cmd, cwd=primary, env=env)
    else:
        raise SystemExit("unknown build system %r for %s" % (bs, name))

    if module.get("ecm") or name.endswith("extra-cmake-modules"):
        allow_ios_in_ecm(tc.host_prefix if host else tc.prefix)


LINK_KEYWORDS = ("PUBLIC", "PRIVATE", "INTERFACE", "LINK_PRIVATE", "LINK_PUBLIC", "LINK_INTERFACE_LIBRARIES")


def normalize_executable_link_signature(srcdir):
    """CMake forbids mixing target_link_libraries(t a b) with
    target_link_libraries(t PRIVATE c) on one target. Qt's finalization and
    the iNux app adapter use the keyword form, so plain calls on the app's
    executables are rewritten to PRIVATE, which is what plain means for an
    executable. Library targets are left alone."""
    executables = set()
    lists = []
    for root, _dirs, files in os.walk(srcdir):
        for f in files:
            if f == "CMakeLists.txt" or f.endswith(".cmake"):
                p = os.path.join(root, f)
                lists.append(p)
                for m in re.finditer(r"add_executable\(\s*([A-Za-z0-9_.+-]+)", open(p, errors="replace").read()):
                    executables.add(m.group(1))
    if not executables:
        return

    def fix(m):
        target, rest = m.group(2), m.group(3)
        first = rest.split()[0] if rest.split() else ""
        if target not in executables or first.upper() in LINK_KEYWORDS:
            return m.group(0)
        return "%s%s PRIVATE%s" % (m.group(1), target, rest)

    pattern = re.compile(r"(target_link_libraries\(\s*)([A-Za-z0-9_.+-]+)((?:\s|\))[^)]*)", re.S)
    for p in lists:
        text = open(p, errors="replace").read()
        new = pattern.sub(fix, text)
        if new != text:
            open(p, "w").write(new)
            log("link signature normalized in", os.path.relpath(p, srcdir))


TARGET_DEF_RE = re.compile(r"add_library\(\s*([A-Za-z0-9_]+::[A-Za-z0-9_]+)\s")
TARGET_REF_RE = re.compile(r"\b((?:Qt6|KF6|KDEGames6)::[A-Za-z0-9_]+)\b")
STATIC_DEPS_MARK = "# iNux: dependencies of a static build"


def fix_static_package_configs(prefix, qt):
    """Static libraries export their private link dependencies, but KDE's
    package config files only load the packages a shared build needs. For
    every package installed in the prefix, find the targets its exported
    files reference but do not define, work out which installed package (or
    Qt module) defines each, and add the missing find_dependency() calls.
    Idempotent; runs before every iOS build."""
    roots = [os.path.join(prefix, "lib", "cmake")]
    if qt:
        roots.append(os.path.join(qt, "lib", "cmake"))
    owner = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for pkg in os.listdir(root):
            d = os.path.join(root, pkg)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f.endswith(".cmake") and "Targets" in f:
                    for t in TARGET_DEF_RE.findall(open(os.path.join(d, f), errors="replace").read()):
                        owner.setdefault(t, pkg)
    root = roots[0]
    if not os.path.isdir(root):
        return
    for pkg in sorted(os.listdir(root)):
        d = os.path.join(root, pkg)
        config = os.path.join(d, pkg + "Config.cmake")
        if not os.path.isfile(config):
            continue
        text = open(config).read()
        if STATIC_DEPS_MARK in text:
            continue
        defined, referenced = set(), set()
        for f in os.listdir(d):
            if f.endswith(".cmake") and "Targets" in f:
                body = open(os.path.join(d, f), errors="replace").read()
                defined.update(TARGET_DEF_RE.findall(body))
                for line in body.splitlines():
                    if "INTERFACE_LINK_LIBRARIES" in line:
                        referenced.update(TARGET_REF_RE.findall(line))
        needed = sorted({owner[t] for t in referenced - defined if t in owner and owner[t] != pkg})
        if not needed:
            continue
        block = "\n%s\ninclude(CMakeFindDependencyMacro)\n%s\n" % (
            STATIC_DEPS_MARK, "\n".join("find_dependency(%s)" % n for n in needed))
        with open(config, "a") as f:
            f.write(block)
        log("added static dependencies to %s: %s" % (pkg, ", ".join(needed)))


def allow_ios_in_ecm(prefix):
    """KDE frameworks list their supported platforms in metainfo.yaml and
    ECM aborts configuration on any other. iOS is not listed, so the abort
    becomes a status message; whether a framework actually builds for iOS
    is decided by compiling it."""
    path = os.path.join(prefix, "share", "ECM", "kde-modules", "KDEMetaInfoPlatformCheck.cmake")
    if not os.path.exists(path):
        log("ECM platform check not found under", prefix)
        return
    text = open(path).read()
    new = text.replace('message(FATAL_ERROR "Your current platform', 'message(STATUS "Your current platform')
    if new != text:
        open(path, "w").write(new)
        log("relaxed ECM platform check for iOS:", path)


# Programs Meson runs at build time. In a cross build they must be macOS
# programs; the iOS prefix has iOS builds of some of them (glib's), which
# Meson would otherwise find through pkg-config.
MESON_HOST_TOOLS = [
    "glib-compile-resources", "glib-compile-schemas", "glib-genmarshal", "glib-mkenums",
    "gdbus-codegen", "gtk4-update-icon-cache", "gtk4-builder-tool", "blueprint-compiler",
    "msgfmt", "msgmerge", "xgettext", "itstool", "desktop-file-validate", "appstreamcli",
]

# Extra link arguments for a GTK app: the UIKit backend's entry point (see
# gdk/uikit/gdkuikitentry.m) and the frameworks it uses.
APP_LINK_ARGS = [
    "-Wl,-e,_gdk_uikit_entry", "-Wl,-u,_gdk_uikit_entry",
    "-framework", "UIKit", "-framework", "QuartzCore", "-framework", "CoreGraphics",
    "-framework", "CoreFoundation", "-framework", "Foundation",
]

# Libraries every iOS link may need: libc++ (HarfBuzz and other C++ code in
# static libraries), iconv and resolv (GLib).
COMMON_LINK_ARGS = ["-lc++", "-liconv", "-lresolv"]


def write_meson_cross(tc, path, extra_link=(), sim_compat=False):
    def lst(flags):
        items = flags.split() if isinstance(flags, str) else list(flags)
        return ", ".join("'%s'" % f.replace("'", "\\'") for f in items)
    # -w: third-party code floods the log with warnings (GLib alone emits
    # hundreds of thousands of lines); errors are what matter here.
    compile_flags = "-I%s/include -w" % tc.prefix
    if tc.sim_compat and sim_compat:
        compile_flags += " -include " + tc.sim_compat
    link_flags = ["-L%s/lib" % tc.prefix] + COMMON_LINK_ARGS + list(extra_link)
    tools = []
    host_path = tc.env(host=True)["PATH"]
    for prog in MESON_HOST_TOOLS:
        found = shutil.which(prog, path=host_path)
        if found:
            tools.append("%s = '%s'" % (prog, found))
    vala = shutil.which("valac", path=host_path)
    vala_args = []
    if vala:
        tools.append("vala = '%s'" % vala)
        # Bindings for libraries Vala does not ship (libadwaita) come from
        # Homebrew; the C side still compiles against the iOS prefix.
        d = os.path.join(os.path.dirname(os.path.dirname(vala)), "share", "vala", "vapi")
        if os.path.isdir(d):
            vala_args += ["--vapidir", d]
    pkgconf = shutil.which("pkg-config") or "pkg-config"
    pcdirs = ["%s/lib/pkgconfig" % tc.prefix, "%s/share/pkgconfig" % tc.prefix]
    # The target flags are part of the compiler command, not just c_args:
    # Meson asks the compiler for its library search path without c_args,
    # and would otherwise get the macOS SDK's (and link macOS libraries).
    target = tc.arch_flags.split()

    def prog(path, *extra):
        return "[%s]" % lst([path] + target + list(extra))
    content = """[binaries]
c = %s
cpp = %s
objc = %s
objcpp = %s
ar = '%s'
strip = '%s'
# --static: every library is static, so links need the private
# dependencies pkg-config lists too.
pkg-config = [%s]
cmake = '%s'
%s

[host_machine]
system = 'darwin'
subsystem = 'ios'
kernel = 'xnu'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'

# For dependencies Meson looks up through CMake: search the iOS SDK and the
# prefix, not the macOS SDK.
[cmake]
CMAKE_SYSTEM_NAME = 'iOS'
CMAKE_OSX_SYSROOT = '%s'
CMAKE_OSX_ARCHITECTURES = 'arm64'
CMAKE_FIND_ROOT_PATH = '%s'
CMAKE_FIND_ROOT_PATH_MODE_LIBRARY = 'ONLY'
CMAKE_FIND_ROOT_PATH_MODE_INCLUDE = 'ONLY'
CMAKE_FIND_ROOT_PATH_MODE_PACKAGE = 'ONLY'

[properties]
needs_exe_wrapper = true
pkg_config_libdir = [%s]
# Answers GLib's configure checks would get by running test programs.
growing_stack = false
have_c99_vsnprintf = true
have_c99_snprintf = true
have_unix98_printf = true
va_val_copy = true
have_proc_self_cmdline = false

[built-in options]
c_args = [%s]
cpp_args = [%s]
objc_args = [%s]
objcpp_args = [%s]
c_link_args = [%s]
cpp_link_args = [%s]
objc_link_args = [%s]
objcpp_link_args = [%s]
vala_args = [%s]
""" % (prog(tc.cc), prog(tc.cxx), prog(tc.cc), prog(tc.cxx),
       xcrun("--find", "ar"), xcrun("--find", "strip"), lst([pkgconf, "--static"]),
       shutil.which("cmake") or "cmake", "\n".join(tools),
       tc.sysroot, tc.prefix,
       lst(pcdirs),
       lst(compile_flags), lst(compile_flags), lst(compile_flags), lst(compile_flags),
       lst(link_flags), lst(link_flags), lst(link_flags), lst(link_flags), lst(vala_args))
    open(path, "w").write(content)
    return path


def record_app_executable(tc, module, builddir):
    """Notes where a Meson-built app's executable is, for the packager."""
    command = module.get("app-target") or ""
    candidates = []
    if command:
        candidates.append(os.path.join(tc.prefix, "bin", command))
    for root, _dirs, files in os.walk(builddir):
        for f in files:
            p = os.path.join(root, f)
            if (not command or f == command) and os.access(p, os.X_OK) and not f.endswith((".py", ".sh")) \
                    and "meson-" not in root and "@" not in root:
                candidates.append(p)
    for c in candidates:
        if os.path.isfile(c):
            with open(c, "rb") as f:
                head = f.read(256)
            # iOS runs only Mach-O programs, and signing tools reject a
            # bundle whose main executable is anything else.
            if head[:4] not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                first = head.split(b"\n", 1)[0].decode("utf-8", "replace")
                kind = "a script (%s)" % first if first.startswith("#!") else "not an iOS program"
                raise SystemExit("the app's command %s is %s; only compiled apps can run on iOS"
                                 % (os.path.basename(c), kind))
            with open(os.path.join(tc.prefix, ".inux-app-exe"), "w") as f:
                f.write(c)
            log("app executable:", c)
            return
    raise SystemExit("could not find the app's executable %r" % command)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--host-prefix", default="")
    ap.add_argument("--qt", default="")
    ap.add_argument("--prepend", action="append", default=[],
                    help="extra modules JSON (a runtime recipe) built before the plan")
    ap.add_argument("--fetch-only", action="store_true",
                    help="only fetch and patch the plan's own modules (for dependency scanning)")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--source-cache", default="src-cache")
    ap.add_argument("--src-root", default="src-build")
    ap.add_argument("--bundle-id", default="com.inux.ported.app")
    ap.add_argument("--display-name", default="")
    a = ap.parse_args()

    plan_path = os.path.abspath(a.plan)
    plan_dir = os.path.dirname(plan_path)
    plan = json.load(open(plan_path))
    jobs = a.jobs or (os.cpu_count() or 4)
    prefix = os.path.abspath(a.prefix)
    host_prefix = os.path.abspath(a.host_prefix or (prefix + "-host"))
    cache = os.path.abspath(a.source_cache)
    srcroot = os.path.abspath(a.src_root)
    os.makedirs(srcroot, exist_ok=True)

    if a.fetch_only:
        for m in plan["modules"]:
            if m.get("missing"):
                continue
            fetch_sources(m, os.path.join(srcroot, m["name"]), cache, plan_dir)
            log("fetched", m["name"])
        return

    modules = []
    for extra in a.prepend:
        modules += json.load(open(extra))["modules"]
    modules += [m for m in plan["modules"] if not m.get("missing")]

    tc = Toolchain(prefix, os.path.abspath(a.qt) if a.qt else "", host_prefix)
    extra_link = []
    for m in modules:
        for item in m.get("inux-app-link", []):
            extra_link.append(item if item.startswith("-") else os.path.join(prefix, item))
    app_settings = {
        "bundle_id": a.bundle_id,
        "display_name": a.display_name or plan.get("app_id") or "App",
        "extra_link": extra_link,
    }

    rebuilding = False
    for m in modules:
        stamp = stamp_path(tc, m)
        digest = module_hash(m)
        # The app itself is always rebuilt: its build tree (the .app bundle the
        # packager picks up) is not part of the cached prefix.
        if (not m.get("app") and not rebuilding and os.path.exists(stamp)
                and open(stamp).read().strip() == digest):
            log("=== %s: up to date (cached)" % m["name"])
            continue
        rebuilding = True
        build_module(m, tc, srcroot, cache, jobs, plan_dir, app_settings)
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(digest)
    log("staged into", prefix)


if __name__ == "__main__":
    main()
