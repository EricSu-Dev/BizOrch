[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[A-Za-z0-9._-]+$')]
    [string]$ReleaseId = '2026.07.28-1',

    [Parameter()]
    [string]$DockerCli = 'docker',

    [Parameter()]
    [string]$OutputRoot,

    [Parameter(Mandatory)]
    [switch]$ConfirmDataWritersStopped
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $ConfirmDataWritersStopped) {
    throw (
        'Stop the local BizOrch API and knowledge-index worker, then rerun with ' +
        '-ConfirmDataWritersStopped.'
    )
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot '.delivery'
}

function Resolve-DockerCli {
    param([string]$RequestedCli)

    if (Test-Path -LiteralPath $RequestedCli -PathType Leaf) {
        return (Resolve-Path -LiteralPath $RequestedCli).Path
    }

    $resolvedCommand = Get-Command -Name $RequestedCli -ErrorAction SilentlyContinue
    if ($null -ne $resolvedCommand) {
        return $resolvedCommand.Source
    }

    $desktopCli = Join-Path $env:LOCALAPPDATA `
        'Programs\DockerDesktop\resources\bin\docker.exe'
    if (Test-Path -LiteralPath $desktopCli -PathType Leaf) {
        return $desktopCli
    }

    throw 'Docker CLI was not found. Start Docker Desktop or pass -DockerCli explicitly.'
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,

        [Parameter(Mandatory)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
    }
}

$docker = Resolve-DockerCli -RequestedCli $DockerCli
$tar = (Get-Command -Name 'tar.exe' -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $OutputRoot)) {
    New-Item -ItemType Directory -Path $OutputRoot | Out-Null
}
$releaseDirectory = Join-Path (Resolve-Path -LiteralPath $OutputRoot).Path $ReleaseId
if (Test-Path -LiteralPath $releaseDirectory) {
    throw "Release directory already exists: $releaseDirectory"
}

$imageNames = @(
    "bizorch-api:$ReleaseId",
    "bizorch-enterprise:$ReleaseId",
    "bizorch-enterprise-mcp:$ReleaseId"
)
foreach ($imageName in $imageNames) {
    $platform = & $docker image inspect $imageName --format '{{.Os}}/{{.Architecture}}'
    if ($LASTEXITCODE -ne 0) {
        throw "Required image does not exist: $imageName"
    }
    if ($platform.Trim() -ne 'linux/amd64') {
        throw "Image must be linux/amd64, got $platform for $imageName"
    }
}

$frontendDist = Join-Path $repositoryRoot '.docker-e2e-frontend-dist\dist'
if (-not (Test-Path -LiteralPath (Join-Path $frontendDist 'index.html'))) {
    throw "Frontend production artifact is missing: $frontendDist"
}

$dataDirectories = @(
    'data\chroma',
    'data\uploads',
    'data\checkpoints'
)
foreach ($relativePath in $dataDirectories) {
    $absolutePath = Join-Path $repositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Container)) {
        throw "Persistent data directory is missing: $absolutePath"
    }
}

$payloadDirectory = Join-Path $releaseDirectory 'payload'
$payloadDeploy = Join-Path $payloadDirectory 'deploy'
$payloadFrontend = Join-Path $payloadDirectory 'frontend'
New-Item -ItemType Directory -Path $payloadDeploy | Out-Null
New-Item -ItemType Directory -Path $payloadFrontend | Out-Null

Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy\compose.yaml') `
    -Destination (Join-Path $payloadDeploy 'compose.yaml')
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy\production.env.example') `
    -Destination (Join-Path $payloadDeploy 'production.env.example')
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy\README.md') `
    -Destination (Join-Path $payloadDeploy 'README.md')
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy\nginx') `
    -Destination $payloadDeploy -Recurse
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy\scripts') `
    -Destination $payloadDeploy -Recurse
Copy-Item -Path (Join-Path $frontendDist '*') `
    -Destination $payloadFrontend -Recurse

$forbiddenFiles = Get-ChildItem -LiteralPath $payloadDirectory -Recurse -File |
    Where-Object {
        $_.Name -eq '.env' -or
        $_.Extension -in @('.pem', '.key', '.p12', '.pfx')
    }
if ($forbiddenFiles) {
    throw "Forbidden secret-bearing file found in release payload: $($forbiddenFiles.FullName)"
}

$releaseMetadata = @(
    "BizOrch release: $ReleaseId"
    "Platform: linux/amd64"
    "API image: $($imageNames[0])"
    "Enterprise image: $($imageNames[1])"
    "MCP image: $($imageNames[2])"
    'Runtime .env is intentionally excluded and must be created on Linux.'
)
Set-Content -LiteralPath (Join-Path $payloadDirectory 'RELEASE.txt') `
    -Value $releaseMetadata -Encoding UTF8

$imageArchive = Join-Path $releaseDirectory "bizorch-images-$ReleaseId.tar"
$deployArchive = Join-Path $releaseDirectory "bizorch-deploy-$ReleaseId.tar.gz"
$dataArchive = Join-Path $releaseDirectory "bizorch-data-$ReleaseId.tar.gz"

Write-Host 'Exporting Docker images...'
Invoke-CheckedCommand -FilePath $docker -ArgumentList (
    @('save', '--output', $imageArchive) + $imageNames
)

Write-Host 'Packing deployment files and frontend...'
Invoke-CheckedCommand -FilePath $tar -ArgumentList @(
    '-czf',
    $deployArchive,
    '-C',
    $payloadDirectory,
    '.'
)

Write-Host 'Packing persistent Chroma, uploads and checkpoints...'
Invoke-CheckedCommand -FilePath $tar -ArgumentList @(
    '-czf',
    $dataArchive,
    '-C',
    $repositoryRoot,
    'data/chroma',
    'data/uploads',
    'data/checkpoints'
)

$checksumLines = @(
    $imageArchive,
    $deployArchive,
    $dataArchive
) | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_
    "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($_))"
}
$checksumContent = ($checksumLines -join "`n") + "`n"
[System.IO.File]::WriteAllText(
    (Join-Path $releaseDirectory 'SHA256SUMS'),
    $checksumContent,
    [System.Text.Encoding]::ASCII
)

Write-Host ''
Write-Host "Release package created: $releaseDirectory"
Get-ChildItem -LiteralPath $releaseDirectory -File |
    Select-Object Name, Length
