#!/usr/bin/env bash
# Deploy an aggregate job site plus one site per role family into a GitHub organisation.
#
#   scripts/deploy_org.sh <org> [--engine-repo NAME] [--engine-only] [--dry-run] [--only backend,mobile] [--no-landing]
#
# Before you run it:
#   1. Create the organisation on github.com (Your organizations -> New). GitHub does NOT let the command line / API
#      create organisations, so this one step is manual. The free plan is enough (all repositories are public).
#   2. `gh auth login` as an owner of that organisation (scopes: repo, workflow).
#
# What it does (safe to run again: existing repositories are updated, not duplicated):
#   <org>/jobboard         the engine: collects jobs for every common tech role, publishes the aggregate site + a data feed
#   <org>/<role>           one light repository per role family (frontend, backend, mobile ...): config + workflow only;
#                          it re-filters the aggregate feed with its own keywords. No scraping, so 13 repositories do not
#                          hit the job sites 13 times.
#   <org>/<org>.github.io  a landing page that links to all of them
# GitHub starts scheduled workflows whenever it likes (lately every 4-8 hours); the script also triggers a first run.
set -euo pipefail

ORG="${1:-}"; shift || true
DRY=0; ONLY=""; LANDING=1; ENGINE="jobboard"; ENGINE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --only) ONLY="$2"; shift ;;
    --no-landing) LANDING=0 ;;
    --engine-repo) ENGINE="$2"; shift ;;
    --engine-only) ENGINE_ONLY=1 ;;       # ship a fix to the engine; the role sites pick it up on their next build
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac; shift
done
[ -n "$ORG" ] || { sed -n '2,20p' "$0"; exit 2; }

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
say() { printf '\n== %s\n' "$*"; }

if [ "$DRY" = 0 ]; then
  gh auth status >/dev/null 2>&1 || { echo "run 'gh auth login' first" >&2; exit 1; }
  gh api "orgs/$ORG" --jq .login >/dev/null 2>&1 || { echo "organisation '$ORG' not found: create it on github.com first (GitHub cannot create organisations from the command line)" >&2; exit 1; }
  role="$(gh api "user/memberships/orgs/$ORG" --jq .role 2>/dev/null || echo none)"
  [ "$role" = admin ] || { echo "you are '$role' in $ORG; an owner is needed to create repositories" >&2; exit 1; }
fi

say "rendering repositories into $WORK"
PRESETS_ARG=()
[ -z "$ONLY" ] || PRESETS_ARG=(--presets ${ONLY//,/ })
PYTHONPATH="$HERE" "$PY" -m jobboard.cli org-render --org "$ORG" --engine-repo "$ENGINE" --out "$WORK" ${PRESETS_ARG[@]+"${PRESETS_ARG[@]}"} > "$WORK/manifest.out"
"$PY" -c "import json,sys; m=json.load(open(sys.argv[1])); print(len(m['profiles']),'role repositories + engine + landing page')" "$WORK/manifest.json"

if [ "$DRY" = 0 ]; then
  LOGIN="$(gh api user --jq .login)"; UID_NUM="$(gh api user --jq .id)"
else
  LOGIN=bot; UID_NUM=0
fi

publish_dir() {   # <dir> <repo> <description> <pages: workflow|branch>
  local dir="$1" repo="$2" desc="$3" pages="$4"
  say "$ORG/$repo"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] create or update $ORG/$repo from $dir, enable Pages ($pages)"; return; fi
  gh repo view "$ORG/$repo" >/dev/null 2>&1 || gh repo create "$ORG/$repo" --public --description "$desc" >/dev/null
  (
    cd "$dir"
    rm -rf .git
    git init -q -b main
    git config user.name "$LOGIN"; git config user.email "${UID_NUM}+${LOGIN}@users.noreply.github.com"
    git add -A
    git commit -q -m "Deploy $repo" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
    git push -q --force "https://github.com/$ORG/$repo.git" main:main
  )
  if [ "$pages" = none ]; then return; fi
  if [ "$pages" = workflow ]; then
    gh api -X POST "repos/$ORG/$repo/pages" -f build_type=workflow >/dev/null 2>&1 \
      || gh api -X PUT "repos/$ORG/$repo/pages" -f build_type=workflow >/dev/null 2>&1 || true
  else
    gh api -X POST "repos/$ORG/$repo/pages" -f build_type=legacy -f 'source[branch]=main' -f 'source[path]=/' >/dev/null 2>&1 || true
  fi
}

run_workflow() {  # <repo> -> prints the run id
  gh workflow run update.yml -R "$ORG/$1" >/dev/null
  sleep 12
  gh run list -R "$ORG/$1" --workflow=update.yml --limit 1 --json databaseId --jq '.[0].databaseId'
}

# 1. the engine first: the role sites read the data feed it publishes
publish_dir "$WORK/engine" "$ENGINE" "Aggregate tech job board + data feed (engine for the role sites)" workflow
say "first run of the aggregate site (a few minutes: it is the only repository that scrapes)"
if [ "$DRY" = 0 ]; then
  id="$(run_workflow "$ENGINE")"
  gh run watch "$id" -R "$ORG/$ENGINE" --interval 20 >/dev/null 2>&1 || true
  [ "$(gh run view "$id" -R "$ORG/$ENGINE" --json conclusion --jq .conclusion)" = success ] \
    || { echo "the aggregate run failed: see https://github.com/$ORG/$ENGINE/actions/runs/$id" >&2; exit 1; }
  echo "   aggregate site: https://$ORG.github.io/$ENGINE/"
fi

if [ "$ENGINE_ONLY" = 1 ]; then say "engine updated; the role sites use it on their next build (or press Run workflow there)"; exit 0; fi

# 2. one light repository per role family
"$PY" -c "
import json, sys
for p in json.load(open(sys.argv[1]))['profiles']:
    print(p['repo'], p['description'], sep='\t')
" "$WORK/manifest.json" > "$WORK/profiles.tsv"
while IFS=$'\t' read -r repo desc; do
  publish_dir "$WORK/$repo" "$repo" "$desc" workflow
  [ "$DRY" = 1 ] || run_workflow "$repo" >/dev/null
done < "$WORK/profiles.tsv"

# 2b. tell the engine which repositories to refresh right after each run (see refresh-role-sites in its workflow). It only
#     acts once a secret ROLE_SITES_TOKEN exists:  gh secret set ROLE_SITES_TOKEN -R "$ORG/$ENGINE"
#     (a fine-grained token, resource owner = the organisation, repositories = the role repos, permission Actions: read and write)
if [ "$DRY" = 0 ]; then
  gh variable set ROLE_REPOS -R "$ORG/$ENGINE" --body "$(cut -f1 "$WORK/profiles.tsv" | sed "s|^|$ORG/|" | tr '\n' ' ')" >/dev/null || true
fi

# 3. landing page at https://<org>.github.io/ and the text shown at github.com/<org>
if [ "$LANDING" = 1 ]; then
  publish_dir "$WORK/landing" "$ORG.github.io" "Tech job boards by role" branch
  publish_dir "$WORK/orgprofile" ".github" "Organisation profile" none
fi

say "done"
echo "  aggregate : https://$ORG.github.io/$ENGINE/"
[ "$LANDING" = 0 ] || echo "  landing   : https://$ORG.github.io/"
echo "  role sites: https://$ORG.github.io/<role>/   (first builds take a minute or two; Pages may need a few more to appear)"
echo "  workflows run on GitHub's own schedule (every 4-8 hours in practice); 'Run workflow' on the Actions tab refreshes at once."
