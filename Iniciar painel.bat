@echo off
title Painel de VLANs
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado neste computador. Instale o Python 3 e tente de novo.
  pause
  exit /b 1
)
python "%~dp0painel_local.py"
if errorlevel 1 pause
