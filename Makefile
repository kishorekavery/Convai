up:
	@echo "Pulling latest image from Docker Hub..."
	docker pull kishore710/convai-app:latest
	@echo "Tagging image with short name..."
	docker tag kishore710/convai-app:latest convai-app:latest
	@echo "Starting containers..."
	docker-compose up -d

down:
	@echo "Stopping containers..."
	docker-compose down

logs:
	docker-compose logs -f
	
restart:
	@echo "Restarting containers..."
	docker-compose restart
