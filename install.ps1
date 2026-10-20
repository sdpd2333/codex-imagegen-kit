[CmdletBinding()]
param(
    [string]$ConfigDir = (Join-Path $env:USERPROFILE '.codex\imagegen'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillSource = Join-Path $here 'skill'
$skillDest = Join-Path $env:USERPROFILE '.codex\skills\relay-imagegen'

if (-not (Test-Path $skillSource)) {
    throw "找不到 skill 目录：$skillSource"
}

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Warning "没有找到 Python 启动器 py。脚本需要 Python 3.9+：https://www.python.org/downloads/（安装时勾选 py launcher）"
}

New-Item -ItemType Directory -Force -Path (Split-Path $skillDest -Parent) | Out-Null
New-Item -ItemType Directory -Force -Path $skillDest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $skillDest 'scripts') | Out-Null
Copy-Item (Join-Path $skillSource 'SKILL.md') (Join-Path $skillDest 'SKILL.md') -Force
Copy-Item (Join-Path $skillSource 'scripts\*.py') (Join-Path $skillDest 'scripts') -Force
Write-Host "已安装技能：$skillDest"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$config = Join-Path $ConfigDir 'config.env'
$localConfig = Join-Path $here 'config.env'
if (-not (Test-Path $config)) {
    if (Test-Path $localConfig) {
        Copy-Item $localConfig $config
        Write-Host "已复制本套件里的 config.env：$config"
    } else {
        Copy-Item (Join-Path $here 'config.example.env') $config
        Write-Host "已创建配置模板：$config"
    }
} else {
    Write-Host "配置文件已存在，未覆盖：$config"
}

$progressPreference = 'SilentlyContinue'
$first = Get-Content $config -TotalCount 200 | Where-Object { $_ -match '^API_KEY=' } | Select-Object -First 1
if ($first -match 'your-image-group-key') {
    Write-Host ""
    Write-Warning "配置里还是示例 key，记得先填图片分组的真实 key。"
}

Write-Host ""
Write-Host "下一步：" -ForegroundColor Cyan
Write-Host "  1) notepad `"$config`"     # 填 BASE_URL / API_KEY"
Write-Host "  2) py `"$skillDest\scripts\imagegen.py`" probe"
Write-Host "  3) 重启 Codex，然后直接说：画一张赛博朋克风格的城市夜景"
Write-Host ""
Write-Host "离线自测（不花钱）：" -ForegroundColor DarkGray
Write-Host "  py `"$here\tests\test_offline.py`""
