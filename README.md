## Project Commands ##

Install vector extension in the schema where vector similarity search will be done

Ex: schema - ai

# ---- LLM Observability Tool ------------------------------------------------------------------------------------------------------ #

## Dependencies
uv pip install arize-phoenix-otel openinference-instrumentation-openai opentelemetry-instrumentation-fastapi openinference-semantic-conventions

## Docker Run Command with auto-restart and auth
docker run -d \
  --name arize_phoenix \
  --restart unless-stopped \
  -e PHOENIX_ENABLE_AUTH=True \
  -e PHOENIX_SECRET=c14ed237a7d37cf298372efc31fdff53b65e9f98a43d9faefab731a53a74daea \
  -p 6006:6006 \
  -p 4317:4317 \
  -p 9090:9090 \
  -e PHOENIX_WORKING_DIR=/mnt/data \
  -v phoenix_data:/mnt/data \
  arizephoenix/phoenix:version-11.23.1

## ------------------------------------------ SERVER SYSTEMCTL ---------------------------------------------------------------------- ##

# CMD 1:
sudo nano /etc/systemd/system/bedrock_conv_ai.service

# File Contents:
[Unit]
Description=SQL Conv AI Bedrock WebService
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/ai/async_conv_ai
ExecStart=/home/ec2-user/ai/async_conv_ai/.venv/bin/python3 -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000
Restart=yes
RestartSec=5
Environment=PATH=/home/ec2-user/ai/async_conv_ai/.venv/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target