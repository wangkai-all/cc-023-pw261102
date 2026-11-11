.PHONY: install data pipeline test backend frontend build run clean

VENV := .venv/bin
FRONTEND := frontend

install:
	python3 -m venv .venv
	$(VENV)/pip install --upgrade pip
	$(VENV)/pip install -r requirements.txt
	cd $(FRONTEND) && npm install --cache ./.npm-cache

# 1) 构造带晚到/撤销/边界/未成熟场景的合成数据 -> data/pipeline.duckdb
data:
	$(VENV)/python -m backend.generate_data

# 2) 点时间特征 + 标签计算，并导出 Parquet -> data/parquet/
pipeline:
	$(VENV)/python -m backend.pipeline

test:
	$(VENV)/python -m pytest backend/tests -q

# 后端 API（含生产构建后的静态托管），监听 127.0.0.1:8000
backend:
	$(VENV)/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload

# 前端开发服务器（Vite，/api 代理到 8000）
frontend:
	cd $(FRONTEND) && npm run dev

build:
	cd $(FRONTEND) && npm run build

# 一键：数据 -> 管道 -> 前端构建
all: data pipeline build

clean:
	rm -rf data $(FRONTEND)/dist $(FRONTEND)/.npm-cache
