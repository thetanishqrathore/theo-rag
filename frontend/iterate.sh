#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e
cd ..
git pull origin redesign
cd frontend

echo "🔄 Starting Sync and Build Process for 'redesign'..."

# 1. Fetch latest and pull changes
echo "📦 Installing npm dependencies..."
npm install

# 3. Build the project
echo "🏗️  Running npm build..."
npm run build

# 4. Final npm install (as requested)
echo "📦 Running final npm install check..."
npm run dev

echo "✅ Done! Your local environment is synced and built."
