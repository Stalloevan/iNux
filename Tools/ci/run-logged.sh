#!/bin/bash
# Runs a command, keeps its full output in the job log, and on failure
# turns the error lines into a GitHub annotation (readable through the
# checks API even when raw logs are not).
#   Tools/ci/run-logged.sh TITLE COMMAND [ARGS...]
TITLE=$1
shift
LOG=${INUX_LOG_FILE:-$(mktemp)}
set -o pipefail
"$@" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  python3 - "$TITLE" "$LOG" <<'EOF'
import re, sys
title, path = sys.argv[1], sys.argv[2]
lines = open(path, errors="replace").read().splitlines()
# Prefer the last failing build step in full (link errors span many lines).
last_failed = max((i for i, l in enumerate(lines) if l.startswith("FAILED:") or "CMake Error" in l), default=None)
if last_failed is not None:
    block = [l[:300] for l in lines[last_failed:last_failed + 90] if not l.startswith("[")]
    msg = "\n".join(block)[:30000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print("::error title=%s (last failure)::%s" % (title, msg))
pat = re.compile(r"(error|FAILED|fatal|Undefined symbol|undefined reference|No such file|cannot find|\*\* BUILD FAILED)", re.I)
picked, seen = [], set()
for i, line in enumerate(lines):
    if pat.search(line) and "warning:" not in line:
        for j in range(max(0, i - 2), min(len(lines), i + 3)):
            if j not in seen:
                seen.add(j)
                picked.append(lines[j][:400])
    if len(picked) > 120:
        break
if not picked:
    picked = [l[:400] for l in lines[-60:]]
msg = "\n".join(picked)[:30000]
msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
print("::error title=%s::%s" % (title, msg))
EOF
fi
[ -z "$INUX_LOG_FILE" ] && rm -f "$LOG"
exit "$rc"
