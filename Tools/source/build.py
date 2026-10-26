#!/usr/bin/env python3
"""Cross-compile a resolved build plan for iOS (device arm64).

  build.py --plan DIR/plan.json --prefix STAGE --qt QT_IOS_DIR
           [--only MODULE] [--jobs N] [--source-cache DIR]

Each module is fetched, configured for iOS with the right build system, and
installed into STAGE so later modules (and the app) find it. STAGE/bin,
STAGE/lib and STAGE/include form the cross sysroot; Qt comes from --qt.

Runs on a macOS runner with Xcode. Only the mechanics live here; which
modules to build comes from the plan (plus Tools/source/recipes for the
runtime frameworks a Flatpak manifest assumes).
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

SDK = "iphoneos"
DEPLOYMENT = "16.0"


def log(*a):
    print(*a, flush=True)


def run(cmd, cwd=None, env=None, check=True):
    if isinstance(cmd, str):
        shown = cmd
    else:
        shown = " ".join(shlex.quote(c) for c in cmd)
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
        for p in (prefix, host_prefix):
            for d in ("bin", "lib", "include", "lib/pkgconfig", "share"):
                os.makedirs(os.path.join(p, d), exist_ok=True)

    def env(self, host=False):
        e = dict(os.environ)
        # Host tools always see the host prefix; iOS modules see both the iOS
        # staging prefix (libraries) and the host prefix (build tools like ecm).
        if host:
            cflags = "-I%s/include" % self.host_prefix
            ldflags = "-L%s/lib" % self.host_prefix
            prefixes = [self.host_prefix]
            qt_bin = os.path.join(self.qt_host(), "bin") if self.qt else None
        else:
            cflags = "%s -fPIC -I%s/include" % (self.arch_flags, self.prefix)
            ldflags = "%s -L%s/lib" % (self.arch_flags, self.prefix)
            prefixes = [self.prefix, self.host_prefix]
            qt_bin = os.path.join(self.qt, "bin") if self.qt else None
        qt_root = self.qt_host() if host else self.qt
        e.update(
            CC=self.cc, CXX=self.cxx,
            CFLAGS=cflags + " " + e.get("CFLAGS", ""),
            CXXFLAGS=cflags + " " + e.get("CXXFLAGS", ""),
            LDFLAGS=ldflags + " " + e.get("LDFLAGS", ""),
            PKG_CONFIG_PATH=":".join("%s/lib/pkgconfig" % p for p in prefixes),
            CMAKE_PREFIX_PATH=":".join(prefixes + ([qt_root] if qt_root else [])),
        )
        # Host build tools must not find the iOS Qt via PATH; only the
        # matching-side Qt bin is exposed.
        path = [os.path.join(self.host_prefix, "bin"), os.path.join(self.prefix, "bin")]
        if qt_bin:
            path.append(qt_bin)
        e["PATH"] = ":".join(path) + ":" + e["PATH"]
        return e

    def cmake_args(self, host=False):
        if host:
            return [
                "-DCMAKE_INSTALL_PREFIX=" + self.host_prefix,
                "-DCMAKE_PREFIX_PATH=" + self.host_prefix,
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_TESTING=OFF",
            ]
        toolchain = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ios.toolchain.cmake")
        args = [
            "-DCMAKE_TOOLCHAIN_FILE=" + toolchain,
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=" + DEPLOYMENT,
            "-DCMAKE_INSTALL_PREFIX=" + self.prefix,
            "-DCMAKE_PREFIX_PATH=%s;%s" % (self.prefix, self.qt or ""),
            "-DCMAKE_FIND_ROOT_PATH=%s;%s" % (self.prefix, self.qt or ""),
            "-DBUILD_SHARED_LIBS=OFF",
            "-DBUILD_TESTING=OFF",
        ]
        if self.qt:
            # Qt's own CMake config and the host tools (moc, qmlcachegen).
            args.append("-DQT_HOST_PATH=" + self.qt_host())
            args.append("-DCMAKE_PREFIX_PATH=%s;%s" % (self.prefix, self.qt))
        return args

    def qt_host(self):
        # aqt installs the host Qt next to the ios one as ../macos.
        host = os.path.join(os.path.dirname(self.qt), "macos")
        return host if os.path.isdir(host) else self.qt


# ------------------------------------------------------- fetching sources --


def fetch_sources(module, srcdir, cache):
    os.makedirs(srcdir, exist_ok=True)
    primary = None
    for s in module.get("sources", []):
        t = s.get("type")
        if t in ("git",):
            primary = primary or srcdir
            ref = s.get("tag") or s.get("commit") or s.get("branch")
            cmd = ["git", "clone", "-q", "--recurse-submodules"]
            if s.get("tag") or s.get("branch"):
                cmd += ["--depth", "1", "--branch", s.get("tag") or s.get("branch")]
            run(cmd + [s["url"], srcdir])
            if s.get("commit"):
                run(["git", "fetch", "-q", "--depth", "1", "origin", s["commit"]], cwd=srcdir, check=False)
                run(["git", "checkout", "-q", s["commit"]], cwd=srcdir, check=False)
        elif t in ("archive", "file") and s.get("url"):
            fn = download(s["url"], cache, s.get("sha256"))
            if t == "archive":
                primary = primary or srcdir
                extract(fn, srcdir, strip=1)
            else:
                shutil.copy(fn, os.path.join(srcdir, s.get("dest-filename") or os.path.basename(s["url"])))
        elif t in ("patch",) and s.get("path"):
            apply_patch(os.path.join(cache, "..", s["path"]), srcdir, s.get("strip-components", 1))
    return primary or srcdir


def download(url, cache, sha256=None):
    os.makedirs(cache, exist_ok=True)
    name = os.path.join(cache, (sha256 or "")[:16] + "-" + os.path.basename(url.split("?")[0]))
    if os.path.exists(name):
        return name
    log("downloading", url)
    tmp = name + ".part"
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    if sha256:
        import hashlib
        h = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        if h != sha256:
            raise SystemExit("sha256 mismatch for %s: got %s" % (url, h))
    os.rename(tmp, name)
    return name


def extract(path, dest, strip=0):
    tmp = dest + ".x"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    if path.endswith((".zip",)):
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(path) as t:
            t.extractall(tmp)
    entries = os.listdir(tmp)
    root = os.path.join(tmp, entries[0]) if strip and len(entries) == 1 else tmp
    for item in os.listdir(root):
        s = os.path.join(root, item)
        d = os.path.join(dest, item)
        if os.path.exists(d):
            shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)
        shutil.move(s, d)
    shutil.rmtree(tmp)


def apply_patch(path, srcdir, strip):
    run(["git", "apply", "-p%d" % strip, os.path.abspath(path)], cwd=srcdir, check=False) or \
        run(["patch", "-p%d" % strip, "-i", os.path.abspath(path)], cwd=srcdir, check=False)


# ----------------------------------------------------------- build systems --


def detect_buildsystem(srcdir, declared):
    if declared and declared != "auto":
        return declared
    if os.path.exists(os.path.join(srcdir, "CMakeLists.txt")):
        return "cmake-ninja"
    if os.path.exists(os.path.join(srcdir, "meson.build")):
        return "meson"
    if os.path.exists(os.path.join(srcdir, "configure")) or os.path.exists(os.path.join(srcdir, "autogen.sh")):
        return "autotools"
    return "simple"


def build_module(module, tc, srcroot, cache, jobs):
    name = module["name"]
    if module.get("missing"):
        log("skipping %s (manifest referenced an external module file we do not have)" % name)
        return
    arch = "aarch64"
    if module.get("only-arches") and arch not in module["only-arches"]:
        log("skipping %s (only-arches=%s)" % (name, module["only-arches"]))
        return
    if module.get("skip-arches") and arch in module["skip-arches"]:
        log("skipping %s (skip-arches)" % name)
        return

    srcdir = os.path.join(srcroot, name)
    primary = fetch_sources(module, srcdir, cache)
    if module.get("subdir"):
        primary = os.path.join(primary, module["subdir"])

    host = bool(module.get("host") or module.get("build-options", {}).get("host"))
    bs = detect_buildsystem(primary, module.get("buildsystem"))
    env = tc.env(host=host)
    opts = module.get("config-opts", [])
    log("=== building %s (%s%s)" % (name, bs, ", host tool" if host else ""))

    if bs in ("cmake-ninja", "cmake"):
        builddir = os.path.join(primary, "_ios-build")
        os.makedirs(builddir, exist_ok=True)
        gen = ["-G", "Ninja"] if bs == "cmake-ninja" and shutil.which("ninja") else []
        run(["cmake", primary] + gen + tc.cmake_args(host=host) + list(opts), cwd=builddir, env=env)
        run(["cmake", "--build", ".", "-j", str(jobs)], cwd=builddir, env=env)
        run(["cmake", "--install", "."], cwd=builddir, env=env)
    elif bs == "meson":
        install_prefix = tc.host_prefix if host else tc.prefix
        builddir = os.path.join(primary, "_ios-build")
        setup = ["meson", "setup", builddir, primary, "--prefix", install_prefix,
                 "--default-library", "static", "--buildtype", "release"]
        if not host:
            setup += ["--cross-file", write_meson_cross(tc, os.path.join(srcroot, name + "-cross.ini"))]
        run(setup + list(opts), env=env)
        run(["meson", "compile", "-C", builddir, "-j", str(jobs)], env=env)
        run(["meson", "install", "-C", builddir], env=env)
    elif bs == "autotools":
        install_prefix = tc.host_prefix if host else tc.prefix
        if not os.path.exists(os.path.join(primary, "configure")) and os.path.exists(os.path.join(primary, "autogen.sh")):
            run(["./autogen.sh"], cwd=primary, env=dict(env, NOCONFIGURE="1"), check=False)
        conf = ["./configure", "--prefix=" + install_prefix, "--enable-static", "--disable-shared"]
        if not host:
            conf += ["--host=arm-apple-darwin"]
        run(conf + list(opts), cwd=primary, env=env)
        run(["make", "-j", str(jobs)], cwd=primary, env=env)
        run(["make", "install"], cwd=primary, env=env)
    elif bs == "simple":
        for cmd in module.get("build-commands", []):
            run(cmd, cwd=primary, env=env)
    else:
        raise SystemExit("unknown build system %r for %s" % (bs, name))


def write_meson_cross(tc, path):
    content = """[binaries]
c = '%s'
cpp = '%s'
ar = '%s'
strip = '%s'
pkg-config = 'pkg-config'

[host_machine]
system = 'darwin'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'

[built-in options]
c_args = [%s]
cpp_args = [%s]
c_link_args = [%s]
cpp_link_args = [%s]
""" % (
        tc.cc, tc.cxx, xcrun("--find", "ar"), xcrun("--find", "strip"),
        _ini_list(tc.arch_flags + " -I%s/include" % tc.prefix),
        _ini_list(tc.arch_flags + " -I%s/include" % tc.prefix),
        _ini_list(tc.arch_flags + " -L%s/lib" % tc.prefix),
        _ini_list(tc.arch_flags + " -L%s/lib" % tc.prefix),
    )
    open(path, "w").write(content)
    return path


def _ini_list(flagstr):
    return ", ".join("'%s'" % f for f in flagstr.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--host-prefix", default="")
    ap.add_argument("--qt", default="")
    ap.add_argument("--prepend", action="append", default=[],
                    help="extra modules JSON (e.g. a runtime recipe) built before the plan")
    ap.add_argument("--only")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--source-cache", default="src-cache")
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    jobs = a.jobs or (os.cpu_count() or 4)
    prefix = os.path.abspath(a.prefix)
    host_prefix = os.path.abspath(a.host_prefix or (prefix + "-host"))
    cache = os.path.abspath(a.source_cache)
    srcroot = os.path.abspath("src-build")
    os.makedirs(srcroot, exist_ok=True)

    modules = []
    for extra in a.prepend:
        modules += json.load(open(extra))["modules"]
    modules += plan["modules"]
    if a.only:
        modules = [m for m in modules if m["name"] == a.only]

    tc = Toolchain(prefix, os.path.abspath(a.qt) if a.qt else "", host_prefix)
    for m in modules:
        build_module(m, tc, srcroot, cache, jobs)
    log("staged into", prefix)


if __name__ == "__main__":
    main()
