#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting Pipeline Reset..."

# 1. Tear down Docker containers and volumes
echo "--------------------------------------"
echo "🛑 Tearing down Docker services (and volumes)..."
docker compose down -v

# 2. Start Docker containers
echo "--------------------------------------"
echo "🐳 Starting Docker services..."
docker compose up -d

# Wait for Postgres to be ready (optional safety pause)
echo "⏳ Waiting 5 seconds for databases to initialize..."
sleep 5

# 3. Run Ingestion
echo "--------------------------------------"
echo "📂 Running Ingestion (Batch)..."
.venv/bin/python -m src.ingestion test/ --batch --tag transcripts

# 4. Run Extraction
echo "--------------------------------------"
echo "⛏️  Running Extraction..."
.venv/bin/python -m src.processing extract --all --tag transcripts

# 5. Run Structure
echo "--------------------------------------"
echo "🏗️  Running Structure Analysis..."
.venv/bin/python -m src.processing structure --all --tag transcripts

# 6. Run Validation
echo "--------------------------------------"
echo "✅ Running Validation..."
.venv/bin/python -m src.processing validate --all --tag transcripts

# 7. Generate Canonical HTML
echo "--------------------------------------"
echo "📄 Generating Canonical HTML..."
.venv/bin/python -m src.processing canonical --all --tag transcripts

echo "--------------------------------------"
echo "🎉 Pipeline Completed Successfully!"
