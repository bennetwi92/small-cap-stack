#!/usr/bin/env bash
# Set the project-board Status for an issue.
#   Usage: scripts/board.sh <issue-number> <Todo|"In Progress"|Done>
#
# Board IDs are hard-coded for project #3 (https://github.com/users/bennetwi92/projects/3).
# If the project is ever recreated, refresh these:
#   gh project view 3 --owner bennetwi92 --format json            # -> project id
#   gh project field-list 3 --owner bennetwi92 --format json      # -> Status field + option ids
set -euo pipefail

OWNER="bennetwi92"
PROJ_NUM="3"
PROJ_ID="PVT_kwHOCGbB5M4Bb_HY"
FIELD_ID="PVTSSF_lAHOCGbB5M4Bb_HYzhWrRtM"

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <issue-number> <Todo|\"In Progress\"|Done>" >&2
  exit 2
fi
num="$1"; status="$2"

case "$status" in
  "Todo")        opt="f75ad846" ;;
  "In Progress") opt="47fc9ee4" ;;
  "Done")        opt="98236657" ;;
  *) echo "unknown status: $status (use Todo | 'In Progress' | Done)" >&2; exit 2 ;;
esac

item=$(gh project item-list "$PROJ_NUM" --owner "$OWNER" --format json --limit 100 \
  | python3 -c "import sys,json;n=int('$num');print(next((i['id'] for i in json.load(sys.stdin)['items'] if i.get('content',{}).get('number')==n),''))")

if [ -z "$item" ]; then
  echo "issue #$num is not on the board (add it: gh project item-add $PROJ_NUM --owner $OWNER --url <url>)" >&2
  exit 1
fi

gh project item-edit --project-id "$PROJ_ID" --id "$item" --field-id "$FIELD_ID" \
  --single-select-option-id "$opt" >/dev/null
echo "#$num -> $status"
