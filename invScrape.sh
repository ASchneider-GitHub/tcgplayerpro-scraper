#!/bin/bash

CARD_NAME="${1:-SEARCHTERM}"
VENDORS=("retrosharkgaming" "redcastle" "goingaming")
LOCKFILE="/tmp/invscrape_$$.lock"

trap 'kill $(jobs -p) 2>/dev/null; rm -f "$LOCKFILE"; exit 143' TERM INT
trap 'rm -f "$LOCKFILE"' EXIT

process_vendor() {
  local STORE="$1"
  local VENDOR_URL="$STORE.tcgplayerpro.com"
  local T_START T_AFTER_SEARCH T_AFTER_SKUS

  local PAYLOAD
  PAYLOAD=$(jq -n --arg name "$CARD_NAME" '{
    query: $name,
    context: {productLineName: "Magic: The Gathering"},
    filters: {productTypeName: ["Cards"]},
    from: 0,
    size: 24
  }')

  T_START=$(date +%s)
  local SEARCH_DATA
  SEARCH_DATA=$(curl -sS --max-time 20 "https://$VENDOR_URL/api/catalog/search" \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' \
    -H 'content-type: application/json' \
    --data-raw "$PAYLOAD")
  if [ $? -ne 0 ]; then
    echo "[$VENDOR_URL] curl failed on catalog/search for query '$CARD_NAME'" >&2
    return
  fi
  if ! echo "$SEARCH_DATA" | jq -e '.products.items' >/dev/null 2>&1; then
    echo "[$VENDOR_URL] unexpected catalog/search response for '$CARD_NAME': $(echo "$SEARCH_DATA" | head -c 200 | tr '\n' ' ')" >&2
    return
  fi
  T_AFTER_SEARCH=$(date +%s)

  local CATALOG_COUNT
  CATALOG_COUNT=$(echo "$SEARCH_DATA" | jq '.products.items | length')

  local JOINED_SKU_IDS
  JOINED_SKU_IDS=$(echo "$SEARCH_DATA" | jq -r '.products.items[].id' | paste -sd, -)

  # No matches for this vendor -- not an error, just nothing to report.
  if [ -z "$JOINED_SKU_IDS" ]; then
    echo "[$VENDOR_URL] query='$CARD_NAME' catalog_items=0 (search_took=$((T_AFTER_SEARCH - T_START))s)" >&2
    return
  fi

  local SKUS_DATA
  SKUS_DATA=$(curl -sS --max-time 20 "https://$VENDOR_URL/api/inventory/skus?productIds=$JOINED_SKU_IDS" \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
  if [ $? -ne 0 ]; then
    echo "[$VENDOR_URL] curl failed on inventory/skus for query '$CARD_NAME' (requested ids: $JOINED_SKU_IDS)" >&2
    return
  fi
  if ! echo "$SKUS_DATA" | jq -e 'type == "array" or type == "object"' >/dev/null 2>&1; then
    echo "[$VENDOR_URL] unexpected inventory/skus response for '$CARD_NAME': $(echo "$SKUS_DATA" | head -c 200 | tr '\n' ' ')" >&2
    return
  fi
  T_AFTER_SKUS=$(date +%s)

  local SKUS_COUNT
  SKUS_COUNT=$(echo "$SKUS_DATA" | jq 'length')
  echo "[$VENDOR_URL] query='$CARD_NAME' catalog_items=$CATALOG_COUNT requested_ids=[$JOINED_SKU_IDS] skus_entries=$SKUS_COUNT search_took=$((T_AFTER_SEARCH - T_START))s skus_took=$((T_AFTER_SKUS - T_AFTER_SEARCH))s" >&2

  local RESULT
  RESULT=$(echo "$SKUS_DATA" | \
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
