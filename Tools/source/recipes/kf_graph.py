#!/usr/bin/env python3
"""Compute the KDE Frameworks a set of projects needs, in build order.

  kf_graph.py --kf-version 6.31.0 --cache DIR SOURCE_DIR... [--json OUT]

Each SOURCE_DIR is a checked-out project (the app, libkdegames, ...). Its
CMake files are scanned for required KF6 components; each framework is then
fetched from KDE's GitHub mirror at the matching tag and scanned the same
way, until the closure is complete. Output: frameworks in dependency order,
plus the non-KF packages and Qt modules each one requires (used to spot
iOS problems such as Qt DBus).

Only REQUIRED dependencies are followed. Optional ones are left unbuilt, so
the matching features are compiled out, which is what we want on iOS.
"""
import argparse
import json
import os
import re
import subprocess
import sys

# KF6 CMake component name -> repository name, where the rule
# "k" + lowercase(component) does not hold.
SPECIAL = {
    "Attica": "attica", "Baloo": "baloo", "BluezQt": "bluez-qt",
    "DNSSD": "kdnssd", "KIO": "kio", "KCMUtils": "kcmutils",
    "Kirigami": "kirigami", "Kirigami2": "kirigami", "KirigamiPlatform": "kirigami",
    "ModemManagerQt": "modemmanager-qt", "NetworkManagerQt": "networkmanager-qt",
    "Prison": "prison", "Purpose": "purpose", "QuickCharts": "kquickcharts",
    "Solid": "solid", "Sonnet": "sonnet", "Syndication": "syndication",
    "SyntaxHighlighting": "syntax-highlighting", "ThreadWeaver": "threadweaver",
    "TextEditor": "ktexteditor", "DocTools": "kdoctools", "UserFeedback": "kuserfeedback",
    "StatusNotifierItem": "kstatusnotifieritem",
}
# Packages provided by other means (not built from a KF repo).
NOT_FRAMEWORKS = {"ECM"}


def repo_for(component):
    if component in SPECIAL:
        return SPECIAL[component]
    return "k" + component.lower()


def cmake_files(root):
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "autotests", "tests", "examples", "docs", "po", "poqm")]
        for f in files:
            if f == "CMakeLists.txt" or f.endswith(".cmake") and dirpath == root:
                out.append(os.path.join(dirpath, f))
    return out


FIND_RE = re.compile(r"find_package\s*\(([^)]*)\)", re.I | re.S)
PROPS_RE = re.compile(r"set_package_properties\s*\(\s*(\S+)\s+PROPERTIES([^)]*)\)", re.I | re.S)


def strip_comments(text):
    return re.sub(r"#[^\n]*", "", text)


def scan(root):
    """Returns (required KF components, required Qt modules, other required pkgs)."""
    kf, qt, other = set(), set(), set()
    optional_found = {}
    texts = []
    for path in cmake_files(root):
        try:
            texts.append(strip_comments(open(path, errors="replace").read()))
        except OSError:
            pass
    text = "\n".join(texts)
    # TYPE REQUIRED promoted through set_package_properties.
    promoted = set()
    for name, props in PROPS_RE.findall(text):
        if re.search(r"TYPE\s+REQUIRED", props):
            promoted.add(name)
    for body in FIND_RE.findall(text):
        toks = body.split()
        if not toks:
            continue
        name = toks[0]
        required = "REQUIRED" in toks or name in promoted
        comps = []
        if "COMPONENTS" in toks:
            i = toks.index("COMPONENTS") + 1
            while i < len(toks) and toks[i] not in ("OPTIONAL_COMPONENTS", "CONFIG", "NO_MODULE", "QUIET", "REQUIRED", "EXACT", "MODULE"):
                if not toks[i].startswith("$"):
                    comps.append(toks[i])
                i += 1
        if name in ("KF6", "KF5"):
            if required:
                kf.update(comps)
        elif name.startswith("KF6") and len(name) > 3:
            if required:
                kf.add(name[3:])
        elif name == "Qt6":
            if required:
                qt.update(comps)
        elif name.startswith("Qt6") and len(name) > 3:
            if required:
                qt.add(name[3:])
        elif name not in ("ECM", "Gettext", "PythonInterp", "Python3", "Doxygen", "QHelpGenerator",
                          "Threads", "PkgConfig", "Perl") and required:
            other.add(name)
    kf.discard("")
    return kf, qt, other


def fetch(repo, tag, cache):
    dest = os.path.join(cache, repo)
    if os.path.isdir(os.path.join(dest, ".git")):
        return dest
    url = "https://github.com/KDE/%s" % repo
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "v" + tag, url, dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("could not fetch %s at v%s: %s\n" % (repo, tag, r.stderr.strip()))
        return None
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kf-version", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--json")
    ap.add_argument("sources", nargs="+")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)

    info = {}   # repo -> dict(deps, qt, other)
    roots = {}
    pending = []
    for s in a.sources:
        kf, qt, other = scan(s)
        roots[os.path.basename(os.path.abspath(s))] = {"kf": sorted(kf), "qt": sorted(qt), "other": sorted(other)}
        pending += [repo_for(c) for c in kf if c not in NOT_FRAMEWORKS]
    while pending:
        repo = pending.pop()
        if repo in info:
            continue
        path = fetch(repo, a.kf_version, a.cache)
        if not path:
            info[repo] = {"deps": [], "qt": [], "other": [], "missing": True}
            continue
        kf, qt, other = scan(path)
        deps = sorted({repo_for(c) for c in kf if c not in NOT_FRAMEWORKS} - {repo})
        info[repo] = {"deps": deps, "qt": sorted(qt), "other": sorted(other)}
        pending += deps

    # Topological order.
    order, state = [], {}

    def visit(r, chain=()):
        if state.get(r) == 2:
            return
        if state.get(r) == 1:
            sys.stderr.write("dependency cycle: %s\n" % " -> ".join(chain + (r,)))
            return
        state[r] = 1
        for d in info.get(r, {}).get("deps", []):
            visit(d, chain + (r,))
        state[r] = 2
        order.append(r)

    for r in sorted(info):
        visit(r)

    result = {"kf_version": a.kf_version, "order": order, "frameworks": info, "roots": roots}
    if a.json:
        with open(a.json, "w") as f:
            json.dump(result, f, indent=2)
    for r in order:
        i = info[r]
        flags = []
        if "DBus" in i["qt"]:
            flags.append("needs QtDBus")
        if i.get("missing"):
            flags.append("NOT FOUND")
        print("%-22s deps=%s qt=%s other=%s %s" % (r, ",".join(i["deps"]) or "-", ",".join(i["qt"]) or "-",
                                                   ",".join(i["other"]) or "-", " ".join(flags)))
    for name, r in roots.items():
        print("ROOT %s: kf=%s qt=%s other=%s" % (name, ",".join(r["kf"]), ",".join(r["qt"]), ",".join(r["other"])))


if __name__ == "__main__":
    main()
