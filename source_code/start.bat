@echo off
cd /d "%~dp0.."
python source_code\launcher.py
if errorlevel 1 pause
