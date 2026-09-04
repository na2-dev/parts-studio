@echo off
rem parts-studio のジョブサーバーを立てる（B-4）。
rem ダブルクリックでも、タスクスケジューラからでも使える。
rem ログは out\server.log へ（Job ごとのログは out\jobs\<id>\log.txt）。
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d %~dp0
if not exist out mkdir out
venv\Scripts\python.exe tools\job_server.py >> out\server.log 2>&1
