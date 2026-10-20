$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path tmp | Out-Null
& './.venv/Scripts/python.exe' -m pytest -q --junitxml=docs/results/pytest.xml
if ($LASTEXITCODE -ne 0) { throw '后端测试失败' }
Push-Location frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
} finally { Pop-Location }
