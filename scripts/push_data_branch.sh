#!/usr/bin/env bash
# Keep the job database on an orphan `data` branch (one commit, replaced every run: the repository does not grow).
# The database holds only excerpts and links (jobboard.publish.scrub_db), never contacts or full texts.
set -euo pipefail

: "${GITHUB_TOKEN:?}" "${GITHUB_REPOSITORY:?}" "${JOBBOARD_HOME:?}"
tmp="$(mktemp -d)"
git -C "$tmp" init -q -b data
cp "$JOBBOARD_HOME/jobs.db" "$tmp/jobs.db"
git -C "$tmp" config user.name "jobboard-bot"
git -C "$tmp" config user.email "jobboard-bot@users.noreply.github.com"
git -C "$tmp" add jobs.db
git -C "$tmp" commit -q -m "data $(date -u +%Y-%m-%dT%H:%MZ)"
# the token only lives in this process; GitHub masks it in logs
git -C "$tmp" push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data:data
echo "state saved ($(du -h "$tmp/jobs.db" | cut -f1))"
