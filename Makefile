# Deploys the version pinned in docker-compose.yml (APP_VERSION, default v10).
# Roll back with:  APP_VERSION=v9 make up
up:
	@echo "Pulling image $${APP_VERSION:-v10} from Docker Hub..."
	docker compose pull
	@echo "Starting containers..."
	docker compose up -d
	@echo "Running:"
	@docker compose images

down:
	@echo "Stopping containers..."
	docker compose down

logs:
	docker compose logs -f
	
restart:
	@echo "Restarting containers..."
	docker compose restart
