#!/usr/bin/env python3
"""Work out how to build a Linux app from source, and write a build plan.

  resolve.py (--app-id ID | --file FILE | --manifest PATH | --repo URL) --out DIR

The app is identified by its Flathub ID (org.example.App), by an uploaded
.flatpak or .AppImage (the ID and source URL are read from it), by a local
Flatpak manifest, or by a source repository URL.

The Flathub repository github.com/flathub/<ID> holds the app's manifest,
which lists every module to build, in order, with its sources and build
system. When no manifest exists, a repository URL found in the app's
AppStream metadata is used as a single CMake/Meson module.

DIR/plan.json describes the modules; DIR/files/ holds patches and other
files the manifest refers to.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

try:
    import yaml
except ImportError:  # PyYAML is optional; JSON manifests still work
    yaml = None


def log(*a):
    print(*a, flush=True)


def git_clone(url, dest, ref=None, depth=1):
    cmd = ["git", "clone", "-q", "--recurse-submodules", "--shallow-submodules"]
    if depth:
        cmd += ["--depth", str(depth)]
    if ref:
        cmd += ["--branch", ref]
    r = subprocess.run(cmd + [url, dest], capture_output=True, text=True)
    return r.returncode == 0


# ------------------------------------------------- reading uploaded files --


def appid_from_flatpak(path):
    """A .flatpak bundle stores its ref (app/ID/ARCH/BRANCH) in plain text."""
    with open(path, "rb") as f:
        head = f.read(1 << 20)
    m = re.search(rb"app/([A-Za-z0-9_.\-]+)/(x86_64|aarch64|arm|i386)/", head)
    return m.group(1).decode() if m else None


def appimage_metadata(path, work):
    """Extracts AppStream metadata and .desktop files from an AppImage."""
    out = os.path.join(work, "appimage")
    os.makedirs(out, exist_ok=True)
    with open(path, "rb") as f:
        data = f.read()
    info = {}
    offset = data.find(b"hsqs", 1)
    while offset > 0:
        r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", out, "-o", str(offset), path,
                            "usr/share/metainfo", "usr/share/appdata", "usr/share/applications", "*.desktop"],
                           capture_output=True)
        if r.returncode == 0 or os.listdir(out):
            break
        offset = data.find(b"hsqs", offset + 1)
    for xml in glob.glob(os.path.join(out, "usr/share/*/*.xml")):
        text = open(xml, errors="replace").read()
        m = re.search(r"<id>\s*([^<\s]+)\s*</id>", text)
        if m and "id" not in info:
            info["id"] = re.sub(r"\.desktop$", "", m.group(1))
        for kind in ("vcs-browser", "homepage", "bugtracker"):
            m = re.search(r'<url type="%s">\s*([^<\s]+)\s*</url>' % kind, text)
            if m:
                info.setdefault("urls", []).append(m.group(1))
    if "id" not in info:
        for desktop in glob.glob(os.path.join(out, "**/*.desktop"), recursive=True):
            name = os.path.basename(desktop)[:-8]
            if name.count(".") >= 2:
                info["id"] = name
                break
    return info


def repo_from_urls(urls):
    for u in urls or []:
        m = re.match(r"https?://(github\.com|gitlab\.com|codeberg\.org|invent\.kde\.org|gitlab\.gnome\.org)/([^/\s]+)/([^/\s#?]+)", u)
        if m:
            return "https://%s/%s/%s" % (m.group(1), m.group(2), m.group(3).removesuffix(".git"))
    return None


# ------------------------------------------------------- manifest parsing --


def load_any(path):
    with open(path) as f:
        text = f.read()
    if path.endswith((".yml", ".yaml")):
        if yaml is None:
            sys.exit("PyYAML is needed for YAML manifests (pip install pyyaml)")
        return yaml.safe_load(text)
    # Flatpak JSON manifests allow /* comments */ and trailing commas.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def find_manifest(repo_dir, app_id):
    for ext in (".json", ".yml", ".yaml"):
        p = os.path.join(repo_dir, app_id + ext)
        if os.path.isfile(p):
            return p
    cands = [p for p in glob.glob(os.path.join(repo_dir, "*")) if p.endswith((".json", ".yml", ".yaml"))]
    for p in cands:
        try:
            d = load_any(p)
        except Exception:
            continue
        if isinstance(d, dict) and ("app-id" in d or "id" in d) and "modules" in d:
            return p
    return None


class Planner:
    def __init__(self, out):
        self.out = out
        self.files = os.path.join(out, "files")
        os.makedirs(self.files, exist_ok=True)
        self.counter = 0

    def keep_file(self, base_dir, rel):
        src = os.path.normpath(os.path.join(base_dir, rel))
        if not os.path.exists(src):
            return None
        self.counter += 1
        dest = os.path.join(self.files, "%03d-%s" % (self.counter, os.path.basename(src)))
        if os.path.isdir(src):
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copyfile(src, dest)
        return os.path.relpath(dest, self.out)

    def sources(self, items, base_dir):
        out = []
        for s in items or []:
            if isinstance(s, str):
                path = os.path.join(base_dir, s)
                loaded = load_any(path)
                out += self.sources(loaded if isinstance(loaded, list) else [loaded], os.path.dirname(path))
                continue
            s = dict(s)
            t = s.get("type")
            if t in ("patch", "file", "dir", "script") or (t == "archive" and "path" in s):
                if "path" in s:
                    s["path"] = self.keep_file(base_dir, s["path"])
                if "paths" in s:
                    s["paths"] = [self.keep_file(base_dir, p) for p in s["paths"]]
            out.append(s)
        return out

    def modules(self, items, base_dir, depth=0):
        """Flattens nested modules: children are built before their parent."""
        out = []
        for m in items or []:
            if isinstance(m, str):
                path = os.path.join(base_dir, m)
                if not os.path.isfile(path):
                    log("module file not found (likely a shared-modules entry):", m)
                    out.append({"name": os.path.basename(m).split(".")[0], "missing": True})
                    continue
                m = load_any(path)
                mbase = os.path.dirname(path)
            else:
                mbase = base_dir
            if not isinstance(m, dict):
                continue
            out += self.modules(m.get("modules"), mbase, depth + 1)
            out.append({
                "name": m.get("name", "module%d" % len(out)),
                "buildsystem": m.get("buildsystem", "autotools"),
                "config-opts": m.get("config-opts", []),
                "make-args": m.get("make-args", []),
                "build-commands": m.get("build-commands", []),
                "subdir": m.get("subdir"),
                "builddir": m.get("builddir", False),
                "build-options": m.get("build-options", {}),
                "only-arches": m.get("only-arches"),
                "skip-arches": m.get("skip-arches"),
                "sources": self.sources(m.get("sources"), mbase),
            })
        return out


def toolkit_of(manifest):
    runtime = manifest.get("runtime", "")
    version = str(manifest.get("runtime-version", ""))
    sdk_ext = " ".join(manifest.get("sdk-extensions", []) or [])
    if "org.kde" in runtime or "qt" in sdk_ext.lower():
        return "qt5" if version.startswith("5") else "qt6"
    if "org.gnome" in runtime:
        return "gtk"
    return "unknown"


def plan_from_manifest(path, out, source_label):
    manifest = load_any(path)
    planner = Planner(out)
    modules = planner.modules(manifest.get("modules"), os.path.dirname(path))
    plan = {
        "source": source_label,
        "app_id": manifest.get("app-id") or manifest.get("id"),
        "runtime": manifest.get("runtime"),
        "runtime_version": str(manifest.get("runtime-version", "")),
        "toolkit": toolkit_of(manifest),
        "command": manifest.get("command"),
        "build_options": manifest.get("build-options", {}),
        "modules": modules,
    }
    return plan


def plan_from_repo(url, app_id, out):
    return {
        "source": url,
        "app_id": app_id,
        "runtime": None,
        "runtime_version": "",
        "toolkit": "unknown",
        "command": None,
        "build_options": {},
        "modules": [{
            "name": (app_id or url.rstrip("/").split("/")[-1]).split(".")[-1],
            "buildsystem": "auto",
            "config-opts": [],
            "make-args": [],
            "build-commands": [],
            "subdir": None,
            "builddir": True,
            "build-options": {},
            "only-arches": None,
            "skip-arches": None,
            "sources": [{"type": "git", "url": url}],
        }],
    }


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--app-id")
    g.add_argument("--file")
    g.add_argument("--manifest")
    g.add_argument("--repo")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    work = os.path.join(out, "work")
    os.makedirs(work)

    app_id, urls = a.app_id, []
    if a.manifest:
        plan = plan_from_manifest(os.path.abspath(a.manifest), out, "manifest:" + a.manifest)
    elif a.repo:
        plan = plan_from_repo(a.repo, None, out)
    else:
        if a.file:
            low = a.file.lower()
            if low.endswith(".flatpak"):
                app_id = appid_from_flatpak(a.file)
            else:
                info = appimage_metadata(a.file, work)
                app_id, urls = info.get("id"), info.get("urls", [])
            log("from file: app id =", app_id, "urls =", urls)
        plan = None
        if app_id:
            repo_dir = os.path.join(work, "flathub")
            if git_clone("https://github.com/flathub/%s" % app_id, repo_dir):
                mpath = find_manifest(repo_dir, app_id)
                if mpath:
                    log("using Flathub manifest", os.path.relpath(mpath, repo_dir))
                    plan = plan_from_manifest(mpath, out, "flathub:" + app_id)
        if plan is None:
            repo = repo_from_urls(urls)
            if not repo:
                sys.exit("No Flathub manifest for %r and no source repository found. "
                         "Try entering the app's Flathub ID or source URL instead." % app_id)
            log("no Flathub manifest; building from", repo)
            plan = plan_from_repo(repo, app_id, out)

    shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(out, "plan.json"), "w") as f:
        json.dump(plan, f, indent=2)
    log(json.dumps({k: v for k, v in plan.items() if k != "modules"}, indent=2))
    for m in plan["modules"]:
        log(" module %-28s %-12s %d sources%s" % (m["name"], m.get("buildsystem"), len(m.get("sources", [])),
                                                  " (missing)" if m.get("missing") else ""))


if __name__ == "__main__":
    main()
