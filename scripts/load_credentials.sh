#!/bin/bash
#
# Load Coinbase credentials from JSON file into environment variables
# Usage: source scripts/load_credentials.sh
#
# This script is meant to be sourced, not executed:
#   source scripts/load_credentials.sh
#   # or
#   . scripts/load_credentials.sh

CRED_FILE="/Users/ahmed/coding-stuff/trader/cb_api.json"

if [ ! -f "$CRED_FILE" ]; then
    echo "❌ Credentials file not found: $CRED_FILE"
    return 1
fi

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo "❌ jq is not installed. Please install it:"
    echo "   brew install jq"
    return 1
fi

# Load credentials
export CB_API_KEY=$(jq -r '.name' "$CRED_FILE")
export CB_API_SECRET=$(jq -r '.privateKey' "$CRED_FILE")

if [ -z "$CB_API_KEY" ] || [ -z "$CB_API_SECRET" ]; then
    echo "❌ Failed to load credentials"
    return 1
fi

echo "✅ Credentials loaded successfully"
echo "   CB_API_KEY: ${CB_API_KEY:0:30}..."
echo "   CB_API_SECRET: ${CB_API_SECRET:0:30}..."
