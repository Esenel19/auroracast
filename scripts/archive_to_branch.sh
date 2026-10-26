#!/usr/bin/env bash
# Copie un dossier de données brutes dans une branche Git dédiée et la pousse.
# Usage : scripts/archive_to_branch.sh <dossier_source> <branche>
# La branche est créée vide (orpheline) au premier lancement.
# Prévu pour GitHub Actions (étape « Archiver dans la branche raw-data »).
set -euo pipefail

SRC="$1"
BRANCH="$2"
STORE="$(mktemp -d)/store"
git worktree prune
trap 'git worktree remove --force "$STORE" 2> /dev/null || true' EXIT

if git ls-remote --exit-code --heads origin "$BRANCH" > /dev/null; then
  git fetch --quiet --depth 1 origin "$BRANCH"
  git worktree add --quiet -B "$BRANCH" "$STORE" FETCH_HEAD
else
  git worktree add --quiet --detach "$STORE"
  git -C "$STORE" checkout --quiet --orphan "$BRANCH"
  git -C "$STORE" rm -r --quiet --force .
fi

cp -R "$SRC"/. "$STORE"/
git -C "$STORE" add -A

if git -C "$STORE" diff --cached --quiet; then
  echo "Rien de nouveau à archiver."
  exit 0
fi

git -C "$STORE" -c user.name="auroracast-bot" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit --quiet -m "Collecte NOAA $(date -u +%Y-%m-%dT%H:%MZ)"
git -C "$STORE" push --quiet origin "$BRANCH"
echo "Archivé dans la branche $BRANCH."
