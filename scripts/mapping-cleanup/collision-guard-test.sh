#!/usr/bin/env bash
# Executes the HEAD-restore collision guard in the rename cleanup scripts.
#
# Guards the failure that produced PR #162-era damage on jazzer: when two
# workflows map different artifacts out of the SAME repo, both generate to the
# same repo-derived filename. The narrower mapping's cleanup script must NOT
# unconditionally delete that file -- if HEAD has a committed version there, it
# belongs to the other workflow and has to be restored.
#
# Validated in a scratch sandbox repo (never against this checkout): "restore to
# HEAD" is meaningless if the local HEAD is itself the corrupted commit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

# Case args: <script> <generated file> <target file> <owner coord> <spurious coord> <kept coord>
run_case() {
  local script="$1" gen="$2" tgt="$3" owner="$4" spurious="$5" kept="$6" mode="$7"
  local box="$SANDBOX/$mode-$(basename "$script" .py)"
  rm -rf "$box"
  mkdir -p "$box/.advisor/mappings" "$box/scripts/mapping-cleanup"
  cp "$REPO_ROOT/scripts/mapping-cleanup/$script" "$box/scripts/mapping-cleanup/"

  git -C "$box" init -q
  git -C "$box" config user.email t@t.t
  git -C "$box" config user.name t

  # The OTHER workflow's legitimate, committed output at the generated path.
  if [ "$mode" = "collision" ]; then
    cat > "$box/.advisor/mappings/$gen" <<JSON
{"slug": "${gen%.json}", "coordinates": ["$owner", "$spurious"], "rewrite": {"1.0.x": {}}}
JSON
  fi
  git -C "$box" add -A
  git -C "$box" commit -qm baseline

  # This run's generator clobbers that path with ITS OWN narrower content.
  cat > "$box/.advisor/mappings/$gen" <<JSON
{"slug": "${gen%.json}", "coordinates": ["$kept", "$spurious"], "rewrite": {"9.9.x": {}}}
JSON

  ( cd "$box" && python3 "scripts/mapping-cleanup/$script" >/dev/null )

  # The renamed target always gets this run's content, spurious coord stripped.
  local got_slug
  got_slug=$(python3 -c "import json;print(json.load(open('$box/.advisor/mappings/$tgt'))['slug'])")
  if [ "$got_slug" != "${tgt%.json}" ]; then
    echo "FAIL [$mode/$script] target slug: want ${tgt%.json}, got $got_slug"; fails=$((fails+1))
  fi
  if ! python3 -c "import json,sys; c=json.load(open('$box/.advisor/mappings/$tgt'))['coordinates']; sys.exit(0 if '$spurious' not in c and '$kept' in c else 1)"; then
    echo "FAIL [$mode/$script] $tgt coords wrong: spurious $spurious kept, or $kept missing"; fails=$((fails+1))
  fi

  if [ "$mode" = "collision" ]; then
    # THE GUARD: the other workflow's committed file must be back, byte-intact.
    if [ ! -f "$box/.advisor/mappings/$gen" ]; then
      echo "FAIL [$mode/$script] $gen was DELETED -- collision guard missing"; fails=$((fails+1)); return
    fi
    # 1.0.x == the committed block, 9.9.x == this run's generated block.
    if ! python3 -c "import json,sys; d=json.load(open('$box/.advisor/mappings/$gen')); sys.exit(0 if list(d['rewrite'])==['1.0.x'] and '$owner' in d['coordinates'] else 1)"; then
      echo "FAIL [$mode/$script] $gen holds generated content, not the committed mapping"; fails=$((fails+1))
    fi
    if git -C "$box" status --porcelain -- ".advisor/mappings/$gen" | grep -q .; then
      echo "FAIL [$mode/$script] $gen still dirty vs HEAD after restore"; fails=$((fails+1))
    fi
  else
    # Nothing committed there: plain deletion is correct, no stray file committed.
    if [ -f "$box/.advisor/mappings/$gen" ]; then
      echo "FAIL [$mode/$script] $gen should have been removed (nothing legit at that path)"; fails=$((fails+1))
    fi
  fi
  echo "ok   [$mode] $script"
}

for mode in collision no-collision; do
  run_case jazzer-junit.py jazzer.json jazzer-junit.json \
    com.code-intelligence:jazzer com.code-intelligence:jazzer-api com.code-intelligence:jazzer-junit "$mode"
  run_case spring-boot-session.py spring-boot.json spring-boot-session.json \
    org.springframework.boot:spring-boot-actuator org.springframework.boot:spring-boot \
    org.springframework.boot:spring-boot-session "$mode"
  run_case shedlock-sql-support.py shedlock.json shedlock-sql-support.json \
    net.javacrumbs.shedlock:shedlock-spring net.javacrumbs.shedlock:shedlock-core \
    net.javacrumbs.shedlock:shedlock-sql-support "$mode"
done

if [ "$fails" -ne 0 ]; then
  echo "FAILED: $fails assertion(s)"
  exit 1
fi
echo "PASS: collision guard holds for all cases"
