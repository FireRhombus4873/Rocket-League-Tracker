@echo off
call .venv\Scripts\activate
pyinstaller RocketLeagueTracker.spec
pause