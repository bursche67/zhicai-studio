$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path 'frontend/dist/index.html')) { throw '请先运行 ./setup.ps1 构建前端' }
& './.venv/Scripts/python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
