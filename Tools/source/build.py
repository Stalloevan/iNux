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

SDK = "iphoneos"
DEPLOYMENT = "16.4"  # C++20 <format> needs std::to_chars(float), iOS 16.3+
HERE = os.path.dirname(os.path.abspath(__file__))
STAMP_VERSION = "3"


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
        self.arch_flags = "-arch arm64 -isysroot %s -miphoneos-version-min=%s" % (self.sysroot, DEPLOYMENT)
        # Extra host prefixes (e.g. Homebrew's gettext for host ki18n).
        self.host_extra = [p for p in os.environ.get("INUX_HOST_PREFIX_PATH", "").split(":") if p]
        for p in (prefix, host_prefix):
            for d in ("bin", "lib", "include", "lib/pkgconfig", "share"):
                os.makedirs(os.path.join(p, d), exist_ok=True)

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
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=" + self.prefix,
            "-DCMAKE_PREFIX_PATH=" + ";".join(prefixes),
            "-DCMAKE_FIND_ROOT_PATH=" + ";".join(prefixes),
            "-DBUILD_SHARED_LIBS=OFF",
            "-DBUILD_TESTING=OFF",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        ]
        if self.qt:
            args.append("-DQT_HOST_PATH=" + self.qt_host())
        if module.get("kde"):
            compat_header = os.path.join(self.prefix, "include", "inux-compat", "inux_qprocess.h")
            compat_libs = [os.path.join(self.prefix, "lib", n) for n in ("libInuxCompat.a", "libintl.a")]
            args += [
                "-DKF6_HOST_TOOLING=" + os.path.join(self.host_prefix, "lib", "cmake"),
                "-DCMAKE_CXX_FLAGS=%s -include %s" % (self.arch_flags, compat_header),
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
        setup = ["meson", "setup", "--wipe" if os.path.isdir(builddir) else "--reconfigure", builddir, primary,
                 "--prefix", install_prefix, "--default-library", "static", "--buildtype", "release"]
        if not os.path.isdir(builddir):
            setup.remove("--reconfigure")
        if not host:
            setup += ["--cross-file", write_meson_cross(tc, os.path.join(srcroot, name + "-cross.ini"))]
        run(setup + opts, env=env)
        run(["meson", "compile", "-C", builddir, "-j", str(jobs)], env=env)
        run(["meson", "install", "-C", builddir], env=env)
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


def write_meson_cross(tc, path):
    def lst(flags):
        return ", ".join("'%s'" % f for f in flags.split())
    content = """[binaries]
c = '%s'
cpp = '%s'
objc = '%s'
ar = '%s'
strip = '%s'
pkg-config = 'pkg-config'

[host_machine]
system = 'darwin'
subsystem = 'ios'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'

[built-in options]
c_args = [%s]
cpp_args = [%s]
objc_args = [%s]
c_link_args = [%s]
cpp_link_args = [%s]
""" % (tc.cc, tc.cxx, tc.cc, xcrun("--find", "ar"), xcrun("--find", "strip"),
       lst(tc.arch_flags + " -I%s/include" % tc.prefix), lst(tc.arch_flags + " -I%s/include" % tc.prefix),
       lst(tc.arch_flags), lst(tc.arch_flags + " -L%s/lib" % tc.prefix), lst(tc.arch_flags + " -L%s/lib" % tc.prefix))
    open(path, "w").write(content)
    return path


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
        if not rebuilding and os.path.exists(stamp) and open(stamp).read().strip() == digest:
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
