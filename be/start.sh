#!/bin/bash
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — please set your GEMINI_API_KEY"
fi
pip install -r requirements.txt --quiet
uvicorn main:app --reload --host 0.0.0.0 --port 8000
