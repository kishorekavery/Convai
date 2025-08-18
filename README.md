## Project Commands ##

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

## Docker Run Command without auto-restart
  docker run \
  -p 6006:6006 \
  -p 4317:4317 \
  -p 9090:9090 \
  -d \
  --restart unless-stopped \
  -e PHOENIX_WORKING_DIR=/mnt/data \
  -v phoenix_data:/mnt/data \
  arizephoenix/phoenix:latest

## --------------------------------------- Command To run Univorn in maintwiz.ai server -------------------------------------------- ##

cd ../home/ubuntu

source venv/bin/activate

cd ai-chat-completion

python3 -m uvicorn main:app --host 0.0.0.0 --port 5001

## ----------------------------------------- COMMAND TO GENERATE EMBEDDING --------------------------------------------------------- ##

# WKDIR
cd ../home/ec2-user/ai/conv_ai

# VENV
source venv/bin/activate

# Script Run
python3 -m database.kbe_table_embedding_generation

## ----------------------------------------- COMMAND TO RUN FASTAPI ---------------------------------------------------------------- ##

## 1.Command to run a single file in local vscode

# WKDIR: presanth@Presanths-Laptop ai_chat_bot_v1_faccode % 
python3 -m dataprocessing.kbe_table_embedding_generation

# 2.Command to install pip dependencies in prelive server
cd /home/ec2-user/ai/conv_ai/
/home/ec2-user/ai/conv_ai/venv/bin/python3 -m pip install -r requirements.txt

# 3.To RUN detached and store output in out.log
nohup /home/ec2-user/ai/conv_ai/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 > out.log 2>&1 &

## ------------------------------------------ SERVER SYSTEMCTL ---------------------------------------------------------------------- ##

# CMD 1:
sudo nano /etc/systemd/system/conv_ai.service

# File Contents:
[Unit]
Description=Conv AI FastAPI Service
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/ai/conv_ai
ExecStart=/home/ec2-user/ai/conv_ai/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target

# CMD 2:
sudo systemctl daemon-reload

# CMD 3:
sudo systemctl start conv_ai.service
sudo systemctl restart conv_ai.service

# CMD 4:
sudo systemctl status conv_ai.service