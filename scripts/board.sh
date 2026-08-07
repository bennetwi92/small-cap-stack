#!/usr/bin/env bash
# Set an issue's Status or Size on the project board.
#   Usage: scripts/board.sh <issue-number> <Backlog|Todo|"In Progress"|Blocked|Done>
#          scripts/board.sh <issue-number> <XS|S|M|L>
#
# The two value spaces don't collide, so one argument is unambiguous: a status word sets
# Status, a size tier sets Size.
#
# Board IDs are hard-coded for project #3 (https://github.com/users/bennetwi92/projects/3).
# If the project is ever recreated, refresh these:
#   gh project view 3 --owner bennetwi92 --format json            # -> project id
#   gh project field-list 3 --owner bennetwi92 --format json      # -> field + option ids
set -euo pipefail

OWNER="bennetwi92"
PROJ_NUM="3"
PROJ_ID="PVT_kwHOCGbB5M4Bb_HY"
STATUS_FIELD="PVTSSF_lAHOCGbB5M4Bb_HYzhWrRtM"
SIZE_FIELD="PVTSSF_lAHOCGbB5M4Bb_HYzhZ7oxU"

usage() {
  echo "usage: $0 <issue-number> <Backlog|Todo|\"In Progress\"|Blocked|Done|XS|S|M|L>" >&2
  exit 2
}

[ "$#" -eq 2 ] || usage
num="$1"; value="$2"

case "$value" in
  "Backlog")     field="$STATUS_FIELD"; opt="9544b6ad" ;;
  "Todo")        field="$STATUS_FIELD"; opt="f75ad846" ;;
  "In Progress") field="$STATUS_FIELD"; opt="47fc9ee4" ;;
  "Blocked")     field="$STATUS_FIELD"; opt="ab0407fa" ;;
  "Done")        field="$STATUS_FIELD"; opt="98236657" ;;
  "XS")          field="$SIZE_FIELD";   opt="2c5c01af" ;;
  "S")           field="$SIZE_FIELD";   opt="dbe01fd8" ;;
  "M")           field="$SIZE_FIELD";   opt="07ea1ac7" ;;
  "L")           field="$SIZE_FIELD";   opt="69a53ac5" ;;
  *) echo "unknown value: $value" >&2; usage ;;
esac

# --limit must exceed the board's item count, or recently-added issues fall outside the
# returned window and look "not on the board" (hit at 101 items with the old limit of 100).
item=$(gh project item-list "$PROJ_NUM" --owner "$OWNER" --format json --limit 1000 \
  | python3 -c "import sys,json;n=int('$num');print(next((i['id'] for i in json.load(sys.stdin)['items'] if i.get('content',{}).get('number')==n),''))")

if [ -z "$item" ]; then
  echo "issue #$num is not on the board (add it: gh project item-add $PROJ_NUM --owner $OWNER --url <url>)" >&2
  exit 1
fi

gh project item-edit --project-id "$PROJ_ID" --id "$item" --field-id "$field" \
  --single-select-option-id "$opt" >/dev/null
echo "#$num -> $value"
