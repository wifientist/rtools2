#!/bin/bash
# Script to fetch and update RuckusONE OpenAPI specification

set -e

OPENAPI_URL="https://api.ruckus.cloud/openapi.json"
OUTPUT_DIR="./api/specs"
OUTPUT_FILE="$OUTPUT_DIR/r1-openapi.json"
BACKUP_FILE="$OUTPUT_DIR/r1-openapi.backup.json"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Backup existing spec if it exists
if [ -f "$OUTPUT_FILE" ]; then
    echo "Backing up existing OpenAPI spec..."
    cp "$OUTPUT_FILE" "$BACKUP_FILE"
fi

# Fetch the OpenAPI spec
echo "Fetching RuckusONE OpenAPI spec from $OPENAPI_URL..."
curl -s -o "$OUTPUT_FILE" "$OPENAPI_URL"

# Check if fetch was successful
if [ $? -eq 0 ]; then
    echo "✓ OpenAPI spec saved to $OUTPUT_FILE"

    # Show some basic info
    if command -v jq &> /dev/null; then
        VERSION=$(jq -r '.info.version // "unknown"' "$OUTPUT_FILE")
        TITLE=$(jq -r '.info.title // "unknown"' "$OUTPUT_FILE")
        ENDPOINT_COUNT=$(jq '[.paths | to_entries[]] | length' "$OUTPUT_FILE")
        echo "  Title: $TITLE"
        echo "  Version: $VERSION"
        echo "  Endpoints: $ENDPOINT_COUNT"
    fi

    # Remove backup if new fetch was successful
    [ -f "$BACKUP_FILE" ] && rm "$BACKUP_FILE"
else
    echo "✗ Failed to fetch OpenAPI spec"
    # Restore backup if it exists
    if [ -f "$BACKUP_FILE" ]; then
        echo "Restoring backup..."
        mv "$BACKUP_FILE" "$OUTPUT_FILE"
    fi
    exit 1
fi
