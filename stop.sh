#!/bin/bash
# Stop all trading bots gracefully
echo "Stopping all bots..."
docker compose down
echo "All bots stopped."
