#!/bin/bash

CARD_NAME="${1:-SEARCHTERM}"
VENDORS=("retrosharkgaming" "redcastle" "goingaming")

# Use a subshell to collect all loop output
{
  for STORE in "${VENDORS[@]}"; do
    VENDOR_URL="$STORE.tcgplayerpro.com"

    PAYLOAD=$(jq -n --arg name "$CARD_NAME" '{
      query: $name,
      context: {productLineName: "Magic: The Gathering"},
      filters: {productTypeName: ["Cards"]},
      from: 0,
      size: 24
    }')

    SEARCH_DATA=$(curl -s "https://$VENDOR_URL/api/catalog/search" \
      -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' \
      -H 'content-type: application/json' \
      --data-raw "$PAYLOAD")

    JOINED_SKU_IDS=$(echo "$SEARCH_DATA" | jq -r '.products.items[].id' | paste -sd, -)

    # Skip to next store if no items are found
    if [ -z "$JOINED_SKU_IDS" ]; then continue; fi

    curl -s "https://$VENDOR_URL/api/inventory/skus?productIds=$JOINED_SKU_IDS" \
      -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' | \
      jq -c --argjson search "$SEARCH_DATA" --arg vendor "$VENDOR_URL" '
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
    '
  done
} #| jq -cs 'add'
