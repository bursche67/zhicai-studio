$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv --without-pip .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
python -m pip --python .venv/Scripts/python.exe install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python依赖安装失败' }
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
} finally { Pop-Location }
Write-Host '安装完成。填写.env模型配置，运行 ./start.ps1'
