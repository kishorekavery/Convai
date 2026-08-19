#!/bin/bash

# Ensure script stops on first error
set -e

ECR_REGISTRY="498398192936.dkr.ecr.ap-south-1.amazonaws.com"
ECR_REPO="maintwiz/askai"
REGION="ap-south-1"

echo "🔐 Logging into Amazon ECR..."
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}

echo "🔨 Building Docker image: ${ECR_REGISTRY}/${ECR_REPO} for linux/amd64..."
# Added --platform linux/amd64 so it runs on standard cloud servers instead of Mac ARM
# Single source of truth for the version. Bump this, not the lines below.
VERSION="${VERSION:-v2}"

echo "Building version ${VERSION}"
docker build --platform linux/amd64 \
  -t "${ECR_REGISTRY}/${ECR_REPO}:latest" \
  -t "${ECR_REGISTRY}/${ECR_REPO}:${VERSION}" .

echo "Pushing ${ECR_REGISTRY}/${ECR_REPO}:latest and :${VERSION}"
docker push "${ECR_REGISTRY}/${ECR_REPO}:latest"
docker push "${ECR_REGISTRY}/${ECR_REPO}:${VERSION}"

echo "✅ Build complete!"
echo "🚀 You can now run it using: ./manage.sh up"
