#!/bin/bash

CONF_FILE="cloudflare.env"
source "$CONF_FILE"

curl -X GET "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/rules/lists/$CF_LIST_ID/items?per_page=100" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json"
