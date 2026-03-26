@echo off
cd /d "%~dp0"
if not exist .env copy .env.example .env
pip install -r requirements.txt --quiet
uvicorn main:app --reload --host 0.0.0.0 --port 8000
