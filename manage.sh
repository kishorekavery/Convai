#!/bin/bash

# Ensure script stops on first error
set -e

ECR_REGISTRY="498398192936.dkr.ecr.ap-south-1.amazonaws.com"
ECR_REPO="maintwiz/askai"
REGION="ap-south-1"

# Help menu
show_help() {
    echo "Usage: ./manage.sh [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  up       Pull the latest image from ECR, and start the containers in detached mode."
    echo "  down     Stop and remove the containers."
    echo "  logs     Follow the logs of the running containers."
    echo "  restart  Restart the running containers."
    echo ""
}

case "$1" in
    up)
        echo "🔐 Logging into Amazon ECR..."
        aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}

        echo "📥 Pulling latest image from ECR..."
        docker pull ${ECR_REGISTRY}/${ECR_REPO}:latest
        
        echo "🏷️ Tagging image with short name..."
        docker tag ${ECR_REGISTRY}/${ECR_REPO}:latest askai:latest
        
        echo "🚀 Starting containers..."
        docker-compose up -d
        ;;
        
    down)
        echo "Stopping containers..."
        docker-compose down
        ;;
        
    logs)
        docker-compose logs -f
        ;;
        
    restart)
        echo "Restarting containers..."
        docker-compose restart
        ;;
        
    *)
        echo "Error: Unknown command '$1'"
        show_help
        exit 1
        ;;
esac
