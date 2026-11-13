# Gera o instalador do Painel de VLANs em três passos:
#   1. desenha o ícone e as imagens do assistente            -> build\arte
#   2. empacota o programa com o Python dentro (PyInstaller)  -> build\dist\PainelVLANs
#   3. monta o instalador gráfico (Inno Setup 6.6 ou mais novo) -> build\saida
#
# Precisa, só no computador que gera: Python 3 com PyInstaller (python -m pip install pyinstaller) e o Inno Setup 6.
# Quem instala não precisa de nada disso.
#
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File installer\gerar-instalador.ps1 [-SoArte]
param([switch]$SoArte)

$ErrorActionPreference = 'Stop'
$aqui = $PSScriptRoot
$raiz = Split-Path -Parent $aqui
$build = Join-Path $raiz 'build'
$arte = Join-Path $build 'arte'

$m = Select-String -Path (Join-Path $raiz 'painel_local.py') -Pattern "^VERSION = '([0-9]+)\.([0-9]+)\.([0-9]+)'"
if (-not $m) { throw 'Não encontrei a linha VERSION em painel_local.py.' }
$g = $m.Matches[0].Groups
$versao = '{0}.{1}.{2}' -f $g[1].Value, $g[2].Value, $g[3].Value
Write-Host "Painel de VLANs $versao"

Write-Host '1/3 Desenhando o ícone e as imagens do instalador...'
. (Join-Path $aqui 'arte.ps1')
[ArtePainel]::Gerar($arte, $versao)
if ($SoArte) { Write-Host "Imagens em $arte"; return }

Write-Host '2/3 Empacotando o programa (PyInstaller)...'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python não encontrado. Instale o Python 3 e depois: python -m pip install pyinstaller'
}
& python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller não encontrado. Instale com: python -m pip install pyinstaller' }

# Version details shown in the file's properties and in the Task Manager (PyInstaller's format, plain ASCII).
$versionFile = Join-Path $build 'versao.txt'
$v = '({0}, {1}, {2}, 0)' -f $g[1].Value, $g[2].Value, $g[3].Value
$texto = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=$v, prodvers=$v, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('FileDescription', 'Painel de VLANs'),
      StringStruct('FileVersion', '$versao'),
      StringStruct('InternalName', 'PainelVLANs'),
      StringStruct('OriginalFilename', 'PainelVLANs.exe'),
      StringStruct('ProductName', 'Painel de VLANs'),
      StringStruct('ProductVersion', '$versao')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"@
[System.IO.File]::WriteAllText($versionFile, $texto, (New-Object System.Text.ASCIIEncoding))

$ico = Join-Path $arte 'painel.ico'
$pagina = Join-Path $raiz 'painel-vlans.html'
$fabricantes = Join-Path $raiz 'fabricantes.tsv'
& python -m PyInstaller --noconfirm --clean --log-level WARN --windowed --onedir --name PainelVLANs `
    --icon $ico --version-file $versionFile `
    --add-data "${pagina}:." --add-data "${fabricantes}:." --add-data "${ico}:." `
    --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'trabalho') --specpath $build `
    (Join-Path $raiz 'painel_local.py')
if ($LASTEXITCODE -ne 0) { throw 'O PyInstaller não conseguiu empacotar o programa.' }

Write-Host '3/3 Montando o instalador (Inno Setup)...'
$iscc = $null
foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs'), ${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    if ($base -and -not $iscc) {
        $candidato = Join-Path $base 'Inno Setup 6\ISCC.exe'
        if (Test-Path $candidato) { $iscc = $candidato }
    }
}
if (-not $iscc) { throw 'Inno Setup 6 não encontrado. Ele é gratuito, no site jrsoftware.org.' }
& $iscc /Q "/DAppVersion=$versao" "/DRaiz=$raiz" (Join-Path $aqui 'painel-vlans.iss')
if ($LASTEXITCODE -ne 0) { throw 'O Inno Setup não conseguiu montar o instalador.' }

$pronto = Get-Item (Join-Path $build "saida\Instalar-Painel-de-VLANs-$versao.exe")
Write-Host ('Pronto: {0} ({1:N1} MB)' -f $pronto.FullName, ($pronto.Length / 1MB))
