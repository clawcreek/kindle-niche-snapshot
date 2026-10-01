#!/bin/bash
# Daily snapshot batch, run by launchd on the maintainer's machine (ai.clawcreek.kindle-niche-snapshot, 03:00 local).
# Hot list (refetched weekly by the skill's 7-day cache), list-page size check, a deep-check batch of at most 12 due
# topics from the top 30, manifest, then commit with the date and push.
# Commits only when topics >= MIN_TOPICS and the batch added at least one card. A failed check exits non-zero with the
# reason in logs/refresh.log and commits nothing. Nothing due (every top topic has a fresh card) is a clean no-op.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# skill checkout whose step-01 scripts are used; the 1.0.3 topic algorithm lives on this worktree until its PR merges
SKILL_REPO="${SKILL_REPO:-$HOME/nrcs-howto-topics}"
MIN_TOPICS=30
MIN_NEW_CARDS=1
LOG="$REPO/logs/refresh.log"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"

mkdir -p "$REPO/logs"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$LOG"; }
discard() { git -C "$REPO" checkout -q -- snapshot 2>/dev/null; git -C "$REPO" clean -fdq -- snapshot 2>/dev/null; }
fail() { log "FAIL: $*"; discard; exit 1; }
field() { python3 -c 'import json,sys; s=json.loads(sys.argv[1]); print(s.get(sys.argv[2], ""))' "$status" "$1"; }

cd "$REPO" || exit 1
log "start (skill repo $SKILL_REPO @ $(git -C "$SKILL_REPO" rev-parse --short HEAD 2>/dev/null))"
[ -z "$(git status --porcelain -- snapshot README.md)" ] || { log "FAIL: snapshot/ or README.md has uncommitted changes"; exit 1; }
git pull -q --ff-only origin main || fail "git pull failed"

status="$(python3 scripts/build_snapshot.py --skill-repo "$SKILL_REPO" 2>> "$LOG")"
rc=$?
log "status: $status"
[ $rc -eq 0 ] || fail "build_snapshot exit $rc ($(field reason))"

topics="$(field topics)"; new_cards="$(field new_cards)"; due="$(field due)"
[ "$topics" -ge $MIN_TOPICS ] || fail "topics $topics < $MIN_TOPICS"
if [ "$due" -eq 0 ]; then log "ok: nothing due (topics=$topics cards=$(field cards)), no commit"; discard; exit 0; fi
[ "$new_cards" -ge $MIN_NEW_CARDS ] || fail "new cards $new_cards < $MIN_NEW_CARDS (due $due, stopped: $(field stopped))"

git add -A snapshot || fail "git add failed"
git -c commit.gpgsign=false commit -q -m "data: snapshot $(date +%F)" || fail "git commit failed"
git push -q origin main || { log "FAIL: git push failed (commit $(git rev-parse --short HEAD) kept locally)"; exit 1; }
log "ok: topics=$topics cards=$(field cards) new=$new_cards commit=$(git rev-parse HEAD)"
