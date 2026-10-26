#!/usr/bin/env python3
"""Run a ported program once under the iNux engine on Linux (CI) and record
the result in manifest.json, so the app can show whether it started.

  smoke.py GENERATED_DIR INUX_RUN [--args "ARGS"] [--expect TEXT] [--timeout S]

Exits non-zero only when --expect is given and not met.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("generated")
    ap.add_argument("runner")
    ap.add_argument("--args", default="--version")
    ap.add_argument("--expect")
    ap.add_argument("--stdin", default="")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()

    mpath = os.path.join(a.generated, "manifest.json")
    with open(mpath) as f:
        manifest = json.load(f)
    rootfs = os.path.abspath(os.path.join(a.generated, "rootfs"))
    entry = manifest.get("entry")
    if not entry:
        print("no entry point; skipping smoke test")
        return 0

    log = os.path.abspath("blink-smoke.log")
    if os.path.exists(log):
        os.unlink(log)
    cmd = [os.path.abspath(a.runner), "-t", str(a.timeout)]
    env = dict(manifest.get("env") or {})
    env.setdefault("COLUMNS", "80")
    env.setdefault("LINES", "24")
    for k in sorted(env):
        cmd += ["-e", "%s=%s" % (k, env[k])]
    args = shlex.split(a.args)
    cmd += [rootfs, entry] + args
    print("+", " ".join(shlex.quote(c) for c in cmd), flush=True)

    host_env = dict(os.environ, BLINK_LOG_FILENAME=log)
    started = time.time()
    try:
        p = subprocess.run(cmd, input=a.stdin.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=a.timeout + 60, env=host_env)
        status, out = p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        status, out = 124, (e.stdout or b"").decode("utf-8", "replace")
    seconds = round(time.time() - started, 1)

    if status != 0 and os.path.exists(log):
        with open(log, "rb") as f:
            tail = f.read()[-3000:].decode("utf-8", "replace")
        out += "\n--- engine log ---\n" + tail

    print(out)
    print("status:", status, "seconds:", seconds)

    manifest["smokeTest"] = {
        "status": status,
        "output": out[-4000:],
        "seconds": seconds,
        "args": args,
    }
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)

    # Leave the guest's /tmp as it was shipped.
    tmp = os.path.join(rootfs, "tmp")
    for name in os.listdir(tmp):
        if name != ".keep":
            p = os.path.join(tmp, name)
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)
    stray = os.path.join(rootfs, "blink.log")
    if os.path.exists(stray):
        os.unlink(stray)

    if a.expect is not None:
        if a.expect not in out or status != 0:
            print("smoke test FAILED: expected %r with status 0" % a.expect)
            return 1
        print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
