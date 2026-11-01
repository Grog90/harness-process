<#
.SYNOPSIS
  Проверка веб-страницы настоящим Chrome/Edge в headless-режиме:
  скриншот, DOM и статистика картинок.

.DESCRIPTION
  Зачем: MCP-браузер moli отдаёт кэшированные скриншоты и не грузит
  ленивые картинки, поэтому визуальную проверку он врёт. Здесь запускается
  настоящий Chrome с чистым профилем, ждёт загрузку и снимает страницу.

.PARAMETER Url
  Адрес страницы.

.PARAMETER Wait
  Сколько секунд виртуального времени ждать загрузку (по умолчанию 20).

.PARAMETER Width
  Ширина окна (по умолчанию 1400).

.PARAMETER Height
  Высота окна (по умолчанию 1000). Для длинных страниц задайте больше.

.PARAMETER KeepDom
  Сохранить HTML страницы рядом со скриншотом.

.EXAMPLE
  ./tools/webshot.ps1 -Url "https://www.youtube.com/@MrBeast/videos" -Wait 25

.EXAMPLE
  ./tools/webshot.ps1 -Url "https://2ip.ru" -Wait 10 -Height 1200
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [int]$Wait = 20,
  [int]$Width = 1400,
  [int]$Height = 1000,
  [switch]$KeepDom
)

$ErrorActionPreference = 'Stop'

$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) { throw "Chrome или Edge не найден в стандартных путях" }

$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root ".browser"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

$slugSrc = ($Url -replace '^https?://', '') -replace '[^A-Za-z0-9]+', '-'
$slug = $slugSrc.Trim('-')
if ($slug.Length -gt 50) { $slug = $slug.Substring(0, 50) }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$png = Join-Path $outDir "$slug-$stamp.png"
$dom = Join-Path $outDir "$slug-$stamp.html"
$profile = Join-Path $env:TEMP ("cr-profile-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$errFile = Join-Path $env:TEMP ("cr-err-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".txt")
$outFile = Join-Path $env:TEMP ("cr-out-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".txt")

$chromeArgs = @(
  "--headless=new",
  "--disable-gpu",
  "--no-sandbox",
  "--hide-scrollbars",
  "--no-first-run",
  "--disable-extensions",
  "--disable-features=Translate,OptimizationHints,MediaRouter",
  "--user-data-dir=$profile",
  "--window-size=$Width,$Height",
  "--virtual-time-budget=$($Wait * 1000)",
  "--screenshot=$png"
)
if ($KeepDom) { $chromeArgs += "--dump-dom" }
$chromeArgs += $Url

try {
  $proc = Start-Process -FilePath $chrome -ArgumentList $chromeArgs -Wait -PassThru `
    -RedirectStandardError $errFile -RedirectStandardOutput $outFile -NoNewWindow

  $result = [ordered]@{
    Url        = $Url
    ExitCode   = $proc.ExitCode
    Screenshot = $null
    Bytes      = 0
    ImgTags    = $null
    ImgWithSrc = $null
    ImageHosts = $null
    Dom        = $null
  }

  if (Test-Path $png) {
    $result.Screenshot = $png
    $result.Bytes = (Get-Item -LiteralPath $png).Length
  }

  if ($KeepDom) {
    $domText = if (Test-Path $outFile) { Get-Content -LiteralPath $outFile -Raw } else { "" }
    Set-Content -LiteralPath $dom -Value $domText -Encoding UTF8
    $result.Dom = $dom

    $imgTags = [regex]::Matches($domText, '<img\b[^>]*>')
    $withSrc = @($imgTags | Where-Object { $_.Value -match 'src\s*=\s*"https?://' })
    $hosts = @{}
    foreach ($tag in $withSrc) {
      $m = [regex]::Match($tag.Value, 'src\s*=\s*"https?://([^/"]+)')
      if ($m.Success) {
        $h = $m.Groups[1].Value
        if (-not $hosts.ContainsKey($h)) { $hosts[$h] = 0 }
        $hosts[$h]++
      }
    }
    $result.ImgTags = $imgTags.Count
    $result.ImgWithSrc = $withSrc.Count
    $result.ImageHosts = ($hosts.GetEnumerator() | Sort-Object Value -Descending |
      ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', '
  }

  [pscustomobject]$result | Format-List
}
finally {
  Remove-Item -Recurse -Force $profile -ErrorAction SilentlyContinue
  Remove-Item -Force $errFile, $outFile -ErrorAction SilentlyContinue
}
