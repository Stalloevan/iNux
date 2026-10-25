#!/usr/bin/env python3
"""Turn an AppImage or Flatpak bundle into a guest root filesystem for iNux.

Usage:
  port.py --src FILE --kind auto|appimage|flatpak --name NAME --out DIR
          [--host-root /] [--no-apt]

Output:
  DIR/rootfs/        guest root: the app under /app, plus every shared
                     library it needs (glibc, ld-linux, ...) copied from this
                     x86-64 machine, and a minimal /etc.
  DIR/manifest.json  entry point, environment, compatibility notes.
  DIR/icon.png       when one is found.

Must run on an x86-64 Linux machine (the CI runner), since that is where
the missing libraries come from. Needs squashfs-tools (AppImage) or
flatpak + ostree (Flatpak). With apt available, libraries the machine does
not have are looked up with apt-file and installed.
"""
import argparse
import configparser
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import uuid
from collections import Counter

ELF_MACHINES = {0x03: "x86", 0x28: "arm", 0x3E: "x86_64", 0xB7: "aarch64", 0xF3: "riscv"}

# Where the guest's dynamic linker looks after LD_LIBRARY_PATH and RUNPATH.
SYSTEM_LIB_DIRS = [
    "/lib/x86_64-linux-gnu",
    "/usr/lib/x86_64-linux-gnu",
    "/lib64",
    "/usr/lib64",
    "/lib",
    "/usr/lib",
]

# Loaded with dlopen() by glibc itself, so they never show up as DT_NEEDED.
GLIBC_DLOPEN = [
    "libnss_files.so.2",
    "libnss_dns.so.2",
    "libresolv.so.2",
    "libgcc_s.so.1",
]

GUEST_USER = "user"
GUEST_HOME = "/home/user"

# ---------------------------------------------------------------- ELF ------


class Elf:
    """Minimal ELF reader: class, machine, type, interpreter, dynamic tags."""

    def __init__(self, path):
        self.path = path
        self.ok = False
        self.machine = None
        self.is64 = False
        self.type = 0
        self.interp = None
        self.needed = []
        self.rpath = []
        self.runpath = []
        self.soname = None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return
        if len(data) < 64 or data[:4] != b"\x7fELF" or data[5] != 1:
            return
        self.is64 = data[4] == 2
        self.type, machine = struct.unpack_from("<HH", data, 16)
        self.machine = ELF_MACHINES.get(machine, "unknown")
        try:
            self._parse(data)
            self.ok = True
        except (struct.error, IndexError, ValueError):
            self.ok = True  # still an ELF, just not one we fully understand

    def _parse(self, d):
        if self.is64:
            phoff = struct.unpack_from("<Q", d, 0x20)[0]
            phentsize, phnum = struct.unpack_from("<HH", d, 0x36)
        else:
            phoff = struct.unpack_from("<I", d, 0x1C)[0]
            phentsize, phnum = struct.unpack_from("<HH", d, 0x2A)
        loads = []
        dyn = None
        for i in range(phnum):
            o = phoff + i * phentsize
            if self.is64:
                p_type, _flags, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<IIQQQQ", d, o)
            else:
                p_type, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<IIIII", d, o)
            if p_type == 1:
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == 2:
                dyn = (p_offset, p_filesz)
            elif p_type == 3:
                self.interp = d[p_offset:p_offset + p_filesz].split(b"\0")[0].decode("utf-8", "replace")
        if not dyn:
            return

        def v2o(addr):
            for vaddr, off, size in loads:
                if vaddr <= addr < vaddr + size:
                    return addr - vaddr + off
            return addr

        entries = []
        off, size = dyn
        step = 16 if self.is64 else 8
        fmt = "<qQ" if self.is64 else "<iI"
        for o in range(off, off + size, step):
            tag, val = struct.unpack_from(fmt, d, o)
            if tag == 0:
                break
            entries.append((tag, val))
        strtab = next((v for t, v in entries if t == 5), None)
        if strtab is None:
            return
        stroff = v2o(strtab)

        def s(i):
            end = d.index(b"\0", stroff + i)
            return d[stroff + i:end].decode("utf-8", "replace")

        for tag, val in entries:
            if tag == 1:
                self.needed.append(s(val))
            elif tag == 14:
                self.soname = s(val)
            elif tag == 15:
                self.rpath += [p for p in s(val).split(":") if p]
            elif tag == 29:
                self.runpath += [p for p in s(val).split(":") if p]


def elf_arch(path):
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return None
    return ELF_MACHINES.get(struct.unpack_from("<H", head, 18)[0], "unknown")


# ------------------------------------------------------------ helpers ------


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def guest_to_host(rootfs, guest_path):
    return os.path.join(rootfs, guest_path.lstrip("/"))


def host_to_guest(rootfs, host_path):
    rel = os.path.relpath(host_path, rootfs)
    return "/" if rel == "." else "/" + rel


def resolve_in_root(rootfs, guest_path, depth=0):
    """Resolve a guest path through guest symlinks; returns a host path or None."""
    if depth > 40:
        return None
    parts = [p for p in guest_path.split("/") if p]
    cur = "/"
    for i, part in enumerate(parts):
        nxt = os.path.join(cur, part)
        host = guest_to_host(rootfs, nxt)
        if os.path.islink(host):
            target = os.readlink(host)
            if not target.startswith("/"):
                target = os.path.normpath(os.path.join(cur, target))
            rest = "/".join(parts[i + 1:])
            return resolve_in_root(rootfs, target + ("/" + rest if rest else ""), depth + 1)
        if not os.path.exists(host):
            return None
        cur = nxt
    return guest_to_host(rootfs, cur)


# --------------------------------------------------------- extraction ------


def squashfs_ok(src, offset):
    r = subprocess.run(["unsquashfs", "-s", "-o", str(offset), src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def appimage_offset(src):
    with open(src, "rb") as f:
        head = f.read(64)
        if head[:4] != b"\x7fELF":
            sys.exit("Not an ELF file")
        if head[4] == 2:
            shoff = struct.unpack_from("<Q", head, 0x28)[0]
            shentsize, shnum = struct.unpack_from("<HH", head, 0x3A)
        else:
            shoff = struct.unpack_from("<I", head, 0x20)[0]
            shentsize, shnum = struct.unpack_from("<HH", head, 0x2E)
        guess = shoff + shentsize * shnum
        f.seek(guess)
        if f.read(4) == b"hsqs" and squashfs_ok(src, guess):
            return guess
    chunk = 1 << 20
    with open(src, "rb") as f:
        pos = 0
        carry = b""
        while True:
            data = f.read(chunk)
            if not data:
                break
            buf = carry + data
            start = 0
            while True:
                i = buf.find(b"hsqs", start)
                if i < 0:
                    break
                cand = pos - len(carry) + i
                if cand > 0 and squashfs_ok(src, cand):
                    return cand
                start = i + 1
            carry = buf[-3:]
            pos += len(data)
    sys.exit("Could not find the squashfs image inside the AppImage (type 1 AppImages are not supported)")


def extract_appimage(src, appdir):
    off = appimage_offset(src)
    print("squashfs offset:", off)
    r = run(["unsquashfs", "-f", "-n", "-q", "-d", appdir, "-o", str(off), src])
    if r.returncode != 0:
        sys.exit("unsquashfs failed")


def extract_flatpak(src, appdir, work):
    """Returns the flatpak metadata as a ConfigParser (or None)."""
    repo = os.path.join(work, "flatpak-repo")
    run(["ostree", "init", "--repo=" + repo, "--mode=archive-z2"], check=False)
    r = run(["flatpak", "build-import-bundle", "--no-update-summary", repo, src])
    if r.returncode != 0:
        sys.exit("flatpak build-import-bundle failed (is this a single-file .flatpak bundle?)")
    refs = subprocess.run(["ostree", "refs", "--repo=" + repo], capture_output=True, text=True).stdout.split()
    if not refs:
        sys.exit("No refs found in the bundle")
    ref = next((x for x in refs if x.startswith("app/")), refs[0])
    print("ref:", ref)
    checkout = os.path.join(work, "checkout")
    r = run(["ostree", "--repo=" + repo, "checkout", "-U", ref, checkout])
    if r.returncode != 0:
        sys.exit("ostree checkout failed")
    meta = None
    mpath = os.path.join(checkout, "metadata")
    if os.path.isfile(mpath):
        meta = configparser.ConfigParser(interpolation=None, strict=False)
        meta.optionxform = str
        meta.read(mpath, encoding="utf-8")
    files = os.path.join(checkout, "files")
    shutil.move(files, appdir)
    export = os.path.join(checkout, "export")
    if os.path.isdir(export):
        shutil.move(export, os.path.join(appdir, ".flatpak-export"))
    return meta


# ------------------------------------------------------ desktop/icon -------


def find_desktop(appdir):
    candidates = [os.path.join(appdir, n) for n in sorted(os.listdir(appdir)) if n.endswith(".desktop")]
    for sub in (".flatpak-export/share/applications", "usr/share/applications", "share/applications"):
        d = os.path.join(appdir, sub)
        if os.path.isdir(d):
            candidates += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".desktop")]
    for path in candidates:
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(appdir)) or not os.path.isfile(real):
            continue
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        try:
            cp.read(real, encoding="utf-8")
        except Exception:
            continue
        if cp.has_section("Desktop Entry"):
            e = cp["Desktop Entry"]
            return {"name": e.get("Name"), "exec": e.get("Exec"), "icon": e.get("Icon"),
                    "terminal": e.get("Terminal", "false").lower() == "true"}
    return {}


def find_icon(appdir, icon_name, out_dir):
    best, best_size = None, -1
    diricon = os.path.join(appdir, ".DirIcon")
    real = os.path.realpath(diricon)
    if os.path.isfile(real) and real.startswith(os.path.realpath(appdir)):
        with open(real, "rb") as f:
            if f.read(8) == b"\x89PNG\r\n\x1a\n":
                best, best_size = real, os.path.getsize(real)
    if icon_name:
        for dirpath, _dirs, files in os.walk(appdir):
            for n in files:
                if n == icon_name + ".png" or n == os.path.basename(icon_name):
                    p = os.path.join(dirpath, n)
                    if os.path.islink(p) or not n.endswith(".png"):
                        continue
                    size = os.path.getsize(p)
                    if size > best_size:
                        best, best_size = p, size
    if best:
        shutil.copyfile(best, os.path.join(out_dir, "icon.png"))
        return True
    return False


# ------------------------------------------------------ entry point --------


def exec_tokens(exec_line):
    try:
        tokens = shlex.split(exec_line or "")
    except ValueError:
        tokens = (exec_line or "").split()
    return [t for t in tokens if not (len(t) == 2 and t.startswith("%"))]


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def is_script(path):
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def find_in_path(rootfs, name, path_dirs):
    if name.startswith("/"):
        host = resolve_in_root(rootfs, name)
        return name if host and os.path.isfile(host) else None
    for d in path_dirs:
        guest = d.rstrip("/") + "/" + name
        host = resolve_in_root(rootfs, guest)
        if host and os.path.isfile(host):
            return guest
    return None


def choose_entry(rootfs, kind, desktop, meta, path_dirs, notes):
    """Returns (guest_program, args)."""
    candidates = []
    if kind == "flatpak" and meta is not None and meta.has_section("Application"):
        cmd = meta["Application"].get("command")
        if cmd:
            candidates.append(([cmd], "flatpak metadata"))
    toks = exec_tokens(desktop.get("exec"))
    if toks:
        candidates.append((toks, ".desktop Exec"))
    if kind == "appimage":
        candidates.append((["/app/AppRun"], "AppRun"))

    script_choice = None
    for toks, why in candidates:
        prog = find_in_path(rootfs, toks[0], path_dirs)
        if not prog:
            continue
        host = resolve_in_root(rootfs, prog)
        if is_elf(host):
            # AppRun's job is setting up the environment, which iNux does
            # itself; prefer the real program when the .desktop names one.
            notes.append("Entry point from %s: %s" % (why, prog))
            return prog, toks[1:]
        if is_script(host) and not script_choice:
            script_choice = (prog, toks[1:], why)

    if script_choice:
        prog, args, why = script_choice
        notes.append("Entry point is a shell script (%s, from %s). Scripts that run other "
                     "commands need process creation, which iOS does not allow." % (prog, why))
        return prog, args

    for d in path_dirs:
        host = resolve_in_root(rootfs, d)
        if not host or not os.path.isdir(host):
            continue
        for n in sorted(os.listdir(host)):
            p = os.path.join(host, n)
            if os.path.isfile(p) and is_elf(p) and os.access(p, os.X_OK):
                notes.append("No usable entry in .desktop; guessed %s/%s" % (d, n))
                return d.rstrip("/") + "/" + n, []
    return None, []


# --------------------------------------------------- library closure -------


def search_dirs_for(rootfs, elf_host_path, elf, ld_library_path):
    origin = host_to_guest(rootfs, os.path.dirname(elf_host_path))

    def expand(p):
        p = p.replace("$ORIGIN", origin).replace("${ORIGIN}", origin)
        p = p.replace("$LIB", "lib64").replace("${LIB}", "lib64")
        p = p.replace("$PLATFORM", "x86_64").replace("${PLATFORM}", "x86_64")
        return os.path.normpath(p) if p.startswith("/") else None

    dirs = []
    if not elf.runpath:
        dirs += [expand(p) for p in elf.rpath]
    dirs += ld_library_path
    dirs += [expand(p) for p in elf.runpath]
    dirs += SYSTEM_LIB_DIRS
    out = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


def find_lib_in_root(rootfs, name, dirs):
    for d in dirs:
        host = resolve_in_root(rootfs, d + "/" + name)
        if host and os.path.isfile(host):
            return host
    return None


def find_lib_on_host(host_root, name):
    for d in SYSTEM_LIB_DIRS:
        p = os.path.join(host_root, d.lstrip("/"), name)
        if os.path.isfile(p):
            e = Elf(p)
            if e.ok and e.machine == "x86_64" and e.is64:
                return d, p
    return None, None


def copy_host_lib(rootfs, guest_dir, host_file, name):
    dest_dir = guest_to_host(rootfs, guest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    if not os.path.exists(dest):
        shutil.copyfile(host_file, dest)  # follows the host symlink
        os.chmod(dest, 0o755)
    return dest


def apt_install_for(missing):
    """Best effort: find Ubuntu packages providing the missing sonames."""
    if not shutil.which("apt-file") or not shutil.which("apt-get"):
        return False
    pkgs = set()
    for name in sorted(missing):
        r = subprocess.run(["apt-file", "search", "-x", "/(usr/)?lib/x86_64-linux-gnu/%s$" % name.replace(".", r"\.").replace("+", r"\+")],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            pkg = line.split(":", 1)[0].strip()
            if pkg and not pkg.endswith("-dbg") and "dbgsym" not in pkg:
                pkgs.add(pkg)
                break
    if not pkgs:
        return False
    print("installing packages for missing libraries:", " ".join(sorted(pkgs)), flush=True)
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    r = run(sudo + ["apt-get", "install", "-y", "-q", "--no-install-recommends"] + sorted(pkgs))
    return r.returncode == 0


def bundle_libraries(rootfs, host_root, ld_library_path, use_apt, notes):
    copied = []
    unresolved = {}
    for attempt in range(2):
        queue = []
        for dirpath, _dirs, files in os.walk(rootfs):
            for n in files:
                p = os.path.join(dirpath, n)
                if not os.path.islink(p) and is_elf(p):
                    queue.append(p)
        for n in GLIBC_DLOPEN:
            queue.append(("dlopen", n))
        seen = set()
        missing = {}
        while queue:
            item = queue.pop()
            if isinstance(item, tuple):
                name = item[1]
                if find_lib_in_root(rootfs, name, SYSTEM_LIB_DIRS):
                    continue
                gdir, hfile = find_lib_on_host(host_root, name)
                if hfile:
                    dest = copy_host_lib(rootfs, gdir, hfile, name)
                    copied.append(host_to_guest(rootfs, dest))
                    queue.append(dest)
                continue
            real = os.path.realpath(item)
            if real in seen:
                continue
            seen.add(real)
            elf = Elf(real)
            if not elf.ok or elf.machine != "x86_64":
                continue
            if elf.interp:
                if not resolve_in_root(rootfs, elf.interp):
                    hfile = os.path.join(host_root, elf.interp.lstrip("/"))
                    if os.path.isfile(hfile):
                        dest = copy_host_lib(rootfs, os.path.dirname(elf.interp), hfile, os.path.basename(elf.interp))
                        copied.append(elf.interp)
                        queue.append(dest)
                    else:
                        missing.setdefault(os.path.basename(elf.interp), []).append(host_to_guest(rootfs, real))
            dirs = search_dirs_for(rootfs, real, elf, ld_library_path)
            for name in elf.needed:
                if "/" in name:
                    continue
                found = find_lib_in_root(rootfs, name, dirs)
                if found:
                    queue.append(found)
                    continue
                gdir, hfile = find_lib_on_host(host_root, name)
                if hfile:
                    dest = copy_host_lib(rootfs, gdir, hfile, name)
                    copied.append(host_to_guest(rootfs, dest))
                    queue.append(dest)
                else:
                    missing.setdefault(name, []).append(host_to_guest(rootfs, real))
        unresolved = missing
        if not missing or attempt == 1 or not use_apt or not apt_install_for(missing):
            break
    if copied:
        notes.append("Added %d system libraries from Ubuntu x86-64 (glibc, loader and others the app expects the OS to provide)." % len(set(copied)))
    if unresolved:
        names = sorted(unresolved)
        notes.append("Missing libraries (the program may fail to start): " + ", ".join(names[:20]) + (" ..." if len(names) > 20 else ""))
    return sorted(set(copied)), {k: sorted(set(v))[:5] for k, v in unresolved.items()}


def copy_support_files(rootfs, host_root):
    """Locale and terminal data the C library and curses programs look for."""
    for rel in ("usr/lib/locale/C.utf8", "usr/share/terminfo/x", "usr/share/terminfo/v",
                "usr/share/terminfo/d", "lib/terminfo/x", "lib/terminfo/v", "lib/terminfo/d",
                "usr/share/zoneinfo/UTC", "etc/ssl/certs/ca-certificates.crt"):
        src = os.path.join(host_root, rel)
        dst = os.path.join(rootfs, rel)
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=False)
        else:
            shutil.copyfile(src, dst)


def write_etc(rootfs):
    etc = os.path.join(rootfs, "etc")
    os.makedirs(etc, exist_ok=True)
    files = {
        "passwd": "root:x:0:0:root:/root:/bin/sh\n%s:x:501:501:%s:%s:/bin/sh\n" % (GUEST_USER, GUEST_USER, GUEST_HOME),
        "group": "root:x:0:\n%s:x:501:\n" % GUEST_USER,
        "hosts": "127.0.0.1 localhost\n::1 localhost\n",
        "resolv.conf": "nameserver 1.1.1.1\nnameserver 8.8.8.8\n",
        "nsswitch.conf": "passwd: files\ngroup: files\nhosts: files dns\n",
        "hostname": "inux\n",
        "machine-id": "0123456789abcdef0123456789abcdef\n",
        "os-release": 'NAME="iNux"\nID=inux\nPRETTY_NAME="iNux (Blink on iOS)"\n',
    }
    for name, content in files.items():
        p = os.path.join(etc, name)
        if os.path.islink(p):
            os.unlink(p)
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(content)
    lt = os.path.join(etc, "localtime")
    if not os.path.lexists(lt) and os.path.exists(os.path.join(rootfs, "usr/share/zoneinfo/UTC")):
        os.symlink("../usr/share/zoneinfo/UTC", lt)
    for d in ("tmp", "home/" + GUEST_USER, "root", "dev", "proc", "SystemRoot", "run/user/501", "var/tmp"):
        os.makedirs(os.path.join(rootfs, d), exist_ok=True)
        keep = os.path.join(rootfs, d, ".keep")
        if not os.listdir(os.path.join(rootfs, d)):
            open(keep, "w").close()


# ------------------------------------------------------- cleanup -----------


def fix_symlinks(rootfs, notes):
    """iOS bundles may only contain relative symlinks that stay inside.

    Absolute links are rewritten relative to the guest root when their
    target exists; anything that escapes or dangles is removed.
    """
    real_root = os.path.realpath(rootfs)
    rewritten = removed = 0
    for dirpath, dirnames, filenames in os.walk(rootfs):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                continue
            target = os.readlink(p)
            if target.startswith("/"):
                host_target = guest_to_host(rootfs, target)
                if os.path.exists(host_target):
                    os.unlink(p)
                    os.symlink(os.path.relpath(host_target, dirpath), p)
                    rewritten += 1
                else:
                    os.unlink(p)
                    removed += 1
                continue
            resolved = os.path.realpath(os.path.join(dirpath, target))
            if not (resolved == real_root or resolved.startswith(real_root + os.sep)) or not os.path.exists(resolved):
                os.unlink(p)
                removed += 1
    if rewritten:
        notes.append("Rewrote %d absolute symlinks as relative links." % rewritten)
    if removed:
        notes.append("Removed %d symlinks that pointed outside the app or nowhere." % removed)


def fix_case_collisions(rootfs, notes):
    """The macOS build machine has a case-insensitive disk."""
    dropped = []
    for dirpath, dirnames, filenames in os.walk(rootfs):
        seen = {}
        for name in sorted(dirnames + filenames):
            key = name.lower()
            if key in seen:
                p = os.path.join(dirpath, name)
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                    if name in dirnames:
                        dirnames.remove(name)
                else:
                    os.unlink(p)
                dropped.append(host_to_guest(rootfs, p))
            else:
                seen[key] = name
    if dropped:
        notes.append("Dropped %d entries whose names differ only by letter case: %s" % (len(dropped), ", ".join(dropped[:5])))


def scan(rootfs):
    archs = Counter()
    count = 0
    total = 0
    for dirpath, _dirs, files in os.walk(rootfs):
        for n in files:
            p = os.path.join(dirpath, n)
            if os.path.islink(p):
                continue
            count += 1
            try:
                total += os.path.getsize(p)
            except OSError:
                continue
            if dirpath.startswith(os.path.join(rootfs, "app")):
                a = elf_arch(p)
                if a:
                    archs[a] += 1
    return count, total, archs


def app_library_path(rootfs):
    """Directories under /app that hold shared libraries, like AppRun sets up."""
    preferred = ["/app/usr/lib/x86_64-linux-gnu", "/app/usr/lib", "/app/usr/lib64",
                 "/app/lib/x86_64-linux-gnu", "/app/lib", "/app/lib64"]
    found = []
    app = os.path.join(rootfs, "app")
    for dirpath, _dirs, files in os.walk(app):
        if any(".so" in n and is_elf(os.path.join(dirpath, n)) for n in files):
            found.append(host_to_guest(rootfs, dirpath))
    ordered = [d for d in preferred if d in found] + sorted(d for d in found if d not in preferred)
    return ordered[:40]


def build_env(rootfs, kind, ld_library_path, meta, name):
    def has(guest):
        return os.path.exists(guest_to_host(rootfs, guest))

    path = [d for d in ("/app/usr/bin", "/app/bin", "/app/usr/sbin", "/app") if has(d)]
    path += ["/usr/local/bin", "/usr/bin", "/bin"]
    data_dirs = [d for d in ("/app/usr/share", "/app/share") if has(d)] + ["/usr/share"]
    env = {
        "HOME": GUEST_HOME,
        "USER": GUEST_USER,
        "LOGNAME": GUEST_USER,
        "SHELL": "/bin/sh",
        "PATH": ":".join(path),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
        "XDG_RUNTIME_DIR": "/run/user/501",
        "XDG_DATA_DIRS": ":".join(data_dirs),
        "XDG_CONFIG_HOME": GUEST_HOME + "/.config",
        "XDG_CACHE_HOME": GUEST_HOME + "/.cache",
        "XDG_DATA_HOME": GUEST_HOME + "/.local/share",
        "LD_LIBRARY_PATH": ":".join(ld_library_path),
        "NO_AT_BRIDGE": "1",
    }
    if kind == "appimage":
        env["APPDIR"] = "/app"
        env["APPIMAGE"] = "/app/AppRun"
        env["ARGV0"] = name
        env["OWD"] = GUEST_HOME
    for schemas in ("/app/usr/share/glib-2.0/schemas", "/app/share/glib-2.0/schemas"):
        if has(schemas):
            env["GSETTINGS_SCHEMA_DIR"] = schemas
            break
    for plugins in ("/app/usr/plugins", "/app/usr/lib/qt5/plugins", "/app/usr/lib/qt6/plugins",
                    "/app/usr/lib/x86_64-linux-gnu/qt5/plugins", "/app/lib/qt5/plugins", "/app/plugins"):
        if has(plugins):
            env["QT_PLUGIN_PATH"] = plugins
            break
    if meta is not None and meta.has_section("Environment"):
        for k, v in meta["Environment"].items():
            env[k] = v
    if kind == "flatpak" and meta is not None and meta.has_section("Application"):
        env["FLATPAK_ID"] = meta["Application"].get("name", name)
    return env


# --------------------------------------------------------------- main ------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--kind", default="auto")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host-root", default="/")
    ap.add_argument("--no-apt", action="store_true")
    args = ap.parse_args()

    kind = args.kind
    if kind == "auto":
        kind = "flatpak" if args.src.lower().endswith(".flatpak") else "appimage"

    out = os.path.abspath(args.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    rootfs = os.path.join(out, "rootfs")
    appdir = os.path.join(rootfs, "app")
    os.makedirs(rootfs)
    work = os.path.join(os.path.dirname(out), "port-work")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)

    meta = None
    if kind == "appimage":
        extract_appimage(args.src, appdir)
    elif kind == "flatpak":
        meta = extract_flatpak(args.src, appdir, work)
    else:
        sys.exit("Unknown kind: " + kind)

    notes = []
    desktop = find_desktop(appdir)
    has_icon = find_icon(appdir, desktop.get("icon"), out)

    count, total, archs = scan(rootfs)
    arch = archs.most_common(1)[0][0] if archs else "none"
    if arch not in ("x86_64", "none"):
        notes.append("This app is built for %s. The iNux engine runs x86-64 programs only." % arch)

    write_etc(rootfs)
    fix_symlinks(rootfs, notes)

    ld_library_path = app_library_path(rootfs)
    copied, unresolved = [], {}
    if arch == "x86_64":
        copied, unresolved = bundle_libraries(rootfs, args.host_root, ld_library_path, not args.no_apt, notes)
        copy_support_files(rootfs, args.host_root)
    def has_libs(guest_dir):
        host = guest_to_host(rootfs, guest_dir)
        return os.path.isdir(host) and any(".so" in n for n in os.listdir(host))

    ld_library_path = ld_library_path + [d for d in SYSTEM_LIB_DIRS if has_libs(d)]

    env = build_env(rootfs, kind, ld_library_path, meta, args.name)
    path_dirs = env["PATH"].split(":")
    entry, entry_args = choose_entry(rootfs, kind, desktop, meta, path_dirs, notes)
    if not entry:
        notes.append("Could not find a program to run.")

    entry_elf = Elf(resolve_in_root(rootfs, entry)) if entry else None
    gui = False
    if entry_elf and entry_elf.ok:
        gui = any(n.startswith(("libX11", "libxcb.so", "libgtk", "libQt5Gui", "libQt6Gui", "libwayland-client", "libSDL2", "libglfw", "libelectron"))
                  for n in entry_elf.needed)
        if not gui and entry_elf.needed:
            # GUI toolkits are often linked one level down.
            for n in entry_elf.needed:
                lib = find_lib_in_root(rootfs, n, ld_library_path)
                if lib:
                    sub = Elf(lib)
                    if any(x.startswith(("libX11", "libxcb.so", "libgtk-", "libwayland-client")) for x in sub.needed):
                        gui = True
                        break
    if desktop.get("terminal"):
        gui = False
    if gui:
        notes.append("This is a graphical app. It needs the iNux display server (X11).")

    fix_symlinks(rootfs, notes)
    fix_case_collisions(rootfs, notes)
    count, total, archs = scan(rootfs)
    if total > 2 * 1024 ** 3:
        notes.append("Payload is over 2 GB; sideloading tools may reject it.")

    manifest = {
        "name": args.name,
        "kind": kind,
        "arch": arch,
        "buildId": uuid.uuid4().hex,
        "entry": entry,
        "args": entry_args,
        "env": env,
        "gui": gui,
        "desktopName": desktop.get("name"),
        "desktopExec": desktop.get("exec"),
        "fileCount": count,
        "totalBytes": total,
        "addedLibraries": copied[:200],
        "missingLibraries": unresolved,
        "binaries": [],
        "notes": notes,
        "hasIcon": has_icon,
        "smokeTest": None,
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("addedLibraries",)}, indent=2))


if __name__ == "__main__":
    main()
