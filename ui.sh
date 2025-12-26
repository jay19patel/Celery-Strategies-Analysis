#!/bin/bash

# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Starting Stock Analysis UI via Docker..."
echo "Access at http://localhost:5050"

# Run the UI using the specific docker-compose file
docker compose -f "$DIR/docker-compose-ui.yml" up --build
