#!/usr/bin/env sh
# Installs the archon-reaper skill for Claude Code by linking it into ~/.claude/skills.
#
#   ./install.sh              symlink: edits in this repo take effect immediately
#   ./install.sh --copy       copy the files instead
#   ./install.sh --force      replace an existing archon-reaper skill
#   ./install.sh --uninstall  remove the installed skill
set -eu

here=$(cd "$(dirname "$0")" && pwd)
source_dir="$here/skills/archon-reaper"
skills_dir="${SKILLS_DIR:-$HOME/.claude/skills}"
target="$skills_dir/archon-reaper"
mode=link; force=0; uninstall=0

for arg in "$@"; do
  case "$arg" in
    --copy) mode=copy ;;
    --force) force=1 ;;
    --uninstall) uninstall=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

remove_installed() {
  if [ -L "$target" ]; then rm "$target"; else rm -rf "$target"; fi
}

if [ "$uninstall" = 1 ]; then
  if [ -e "$target" ] || [ -L "$target" ]; then remove_installed; echo "Removed $target"
  else echo "Nothing installed at $target"; fi
  exit 0
fi

[ -f "$source_dir/SKILL.md" ] || { echo "Cannot find $source_dir/SKILL.md" >&2; exit 1; }
mkdir -p "$skills_dir"

if [ -e "$target" ] || [ -L "$target" ]; then
  [ "$force" = 1 ] || { echo "$target already exists. Re-run with --force to replace it." >&2; exit 1; }
  remove_installed
fi

if [ "$mode" = copy ]; then
  cp -R "$source_dir" "$target"; echo "Copied skill to $target"
else
  ln -s "$source_dir" "$target"; echo "Linked $target -> $source_dir"
fi
echo "Restart Claude Code (or start a new session) so it picks up the skill."
