#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🔨 Building Docker image: kishore710/convai-app for linux/amd64..."
# Added --platform linux/amd64 so it runs on standard cloud servers instead of Mac ARM
docker build --platform linux/amd64 -t kishore710/convai-app:latest -t kishore710/convai-app:v3 .

echo " Pushing Docker image: kishore710/convai-app:..."
docker push kishore710/convai-app:latest
docker push kishore710/convai-app:v3

echo "✅ Build complete!"
echo "🚀 You can now run it using: docker-compose up -d"
