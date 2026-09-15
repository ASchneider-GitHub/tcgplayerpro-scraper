#!/bin/bash

CARD_NAME="${1:-SEARCHTERM}"
VENDORS=("retrosharkgaming" "redcastle" "goingaming")
LOCKFILE="/tmp/invscrape_$$.lock"

trap 'kill $(jobs -p) 2>/dev/null; rm -f "$LOCKFILE"; exit 143' TERM INT
trap 'rm -f "$LOCKFILE"' EXIT

process_vendor() {
  local STORE="$1"
  local VENDOR_URL="$STORE.tcgplayerpro.com"

  local PAYLOAD
  PAYLOAD=$(jq -n --arg name "$CARD_NAME" '{
    query: $name,
    context: {productLineName: "Magic: The Gathering"},
    filters: {productTypeName: ["Cards"]},
    from: 0,
    size: 24
  }')

  local SEARCH_DATA
  SEARCH_DATA=$(curl -s "https://$VENDOR_URL/api/catalog/search" \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' \
    -H 'content-type: application/json' \
    --data-raw "$PAYLOAD")

  local JOINED_SKU_IDS
  JOINED_SKU_IDS=$(echo "$SEARCH_DATA" | jq -r '.products.items[].id' | paste -sd, -)

  # Skip this vendor if no items are found
  if [ -z "$JOINED_SKU_IDS" ]; then return; fi

  local RESULT
  RESULT=$(curl -s "https://$VENDOR_URL/api/inventory/skus?productIds=$JOINED_SKU_IDS" \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' | \
    jq -c --argjson search "$SEARCH_DATA" --arg vendor "$VENDOR_URL" --arg query "$CARD_NAME" '
    ( $search.products.items | reduce .[] as $i ({}; .[($i.id|tostring)] = {
        name: $i.name,
        productUrlName: $i.productUrlName,
        rarityName: $i.rarityName,
        setName: $i.setName,
        setUrlName: $i.setUrlName
    }) ) as $catalog |

    [ .[] | .skus[] | . as $sku |
      ($catalog[($sku.productId|tostring)]) as $meta |
      $meta + {
        query: $query,
        vendor: ($vendor | split(".")[0]),
        productId: $sku.productId,
        conditionName: $sku.conditionName,
        languageName: $sku.languageName,
        price: $sku.price,
        quantity: $sku.quantity,
        isFoil: $sku.isFoil,
        storeUrl: ("https://" + $vendor + "/catalog/magic/" + $meta.setUrlName + "/" + $meta.productUrlName + "/" + ($sku.productId|tostring))
      }
    ]
  ')

  # flock serializes only this final write so concurrent vendors can never
  # interleave partial JSON lines on the shared stdout pipe (a raw echo/printf
  # isn't guaranteed atomic once a line exceeds PIPE_BUF). Store under /tmp
  # (not the repo path) since flock on a WSL/DrvFS-backed file is unreliable.
  if [ -n "$RESULT" ] && [ "$RESULT" != "[]" ]; then
    flock -x "$LOCKFILE" printf '%s\n' "$RESULT"
  fi
}

for STORE in "${VENDORS[@]}"; do
  process_vendor "$STORE" &
done
wait
