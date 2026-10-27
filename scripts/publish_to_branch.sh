#!/usr/bin/env bash
# Publie un dossier comme contenu UNIQUE d'une branche Git (un seul commit, poussé en force).
# Sert aux tables propres : elles sont recalculées entièrement à chaque fois à partir de
# raw-data, donc inutile de garder leur historique (la branche ne grossit pas).
# Usage : scripts/publish_to_branch.sh <dossier> <branche> [libellé du commit]
# Prévu pour GitHub Actions.
set -euo pipefail

SRC="$1"
BRANCH="$2"
LABEL="${3:-Publication}"
STORE="$(mktemp -d)/store"
TMP="tmp-publish-$BRANCH"
cleanup() {
  git worktree remove --force "$STORE" 2> /dev/null || true
  git branch -D "$TMP" > /dev/null 2>&1 || true
}
git worktree prune
cleanup
trap cleanup EXIT

git worktree add --quiet --detach "$STORE"
git -C "$STORE" checkout --quiet --orphan "$TMP"
git -C "$STORE" rm -r --quiet --force --ignore-unmatch .
cp -R "$SRC"/. "$STORE"/
git -C "$STORE" add -A
git -C "$STORE" -c user.name="auroracast-bot" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit --quiet -m "$LABEL $(date -u +%Y-%m-%dT%H:%MZ)"
git -C "$STORE" push --quiet --force origin "HEAD:refs/heads/$BRANCH"
echo "Publié dans la branche $BRANCH."
