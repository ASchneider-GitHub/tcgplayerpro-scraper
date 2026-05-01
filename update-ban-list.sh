#!/bin/bash

CONF_FILE="$HOME/tcgplayerpro-scraper/cloudflare.env"
LOG_FILE="$HOME/tcgplayerpro-scraper/banned-ips.txt"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

source "$CONF_FILE"

echo "------------------------------------------------------------"
echo "Run started at: $TIMESTAMP"
echo "------------------------------------------------------------"

echo "Step 1: Scraping logs for new bad actors..."
# Get IPs from logs, append to file
docker logs tcgplayerpro-scraper 2>&1 | grep -E " 404 | 405 " | awk '{print $1}' >> $LOG_FILE

echo "Step 2: Cleaning and deduplicating list..."
# Sort unique and save back to the same file
sort -u $LOG_FILE -o $LOG_FILE

echo "Step 3: Preparing payload..."
# Convert new-line file to Cloudflare JSON format
PAYLOAD=$(awk 'NF {printf "{\"ip\":\"%s\"},", $1}' $LOG_FILE | sed 's/,$//')
PAYLOAD="[$PAYLOAD]"

echo "Step 4: Syncing to Cloudflare..."
RESPONSE=$(curl -s -w "\nHTTP Status: %{http_code}\n" -X PUT \
     "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/rules/lists/$CF_LIST_ID/items" \
     -H "Authorization: Bearer $CF_TOKEN" \
     -H "Content-Type: application/json" \
     -d "$PAYLOAD")

# Check if the HTTP status was 200 (Success)
if [[ $RESPONSE == *"HTTP Status: 200"* ]]; then
    echo "Success! Cloudflare list updated with $(wc -l < $LOG_FILE) IPs."
else
    echo "Error updating Cloudflare at $TIMESTAMP:"
    echo "$RESPONSE"
fi
echo "------------------------------------------------------------"
echo ""
