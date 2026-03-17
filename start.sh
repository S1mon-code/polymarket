#!/bin/bash
# Start all trading bots
echo "Starting Polymarket Multi-Bot Trading System..."
echo "DRY_RUN=${DRY_RUN:-true}"
docker compose up -d --build
docker compose logs -f
