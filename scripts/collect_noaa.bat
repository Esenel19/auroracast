@echo off
REM Lance la collecte NOAA (appele par le Planificateur de taches Windows)
cd /d "%~dp0.."
if not exist data\raw mkdir data\raw
call .venv\Scripts\activate.bat
python -m auroracast.collect.noaa >> data\raw\collect.log 2>&1
