@echo off
title Gerar o instalador do Painel de VLANs
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\gerar-instalador.ps1"
if errorlevel 1 (
  echo.
  echo Nao foi possivel gerar o instalador. Veja a mensagem acima.
  pause
  exit /b 1
)
echo.
echo O instalador esta na pasta build\saida.
pause
