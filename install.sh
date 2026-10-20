#!/usr/bin/env bash
# relay-imagegen installer for macOS / Linux.
# Windows users: run install.ps1 instead.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skill_dest="$codex_home/skills/relay-imagegen"
config_dir="$codex_home/imagegen"
config_file="$config_dir/config.env"

mkdir -p "$skill_dest/scripts" "$config_dir"
cp "$here/skill/SKILL.md" "$skill_dest/SKILL.md"
cp "$here"/skill/scripts/*.py "$skill_dest/scripts/"

if [ ! -f "$config_file" ]; then
  cp "$here/config.example.env" "$config_file"
  echo "created config: $config_file"
  echo "  -> fill in BASE_URL and API_KEY before using it"
else
  echo "kept existing config: $config_file"
fi

echo
echo "installed skill: $skill_dest"
echo "next steps:"
echo "  1) edit $config_file"
echo "  2) python3 \"$skill_dest/scripts/imagegen.py\" probe"
echo "  3) restart Codex, then ask it to draw something"
