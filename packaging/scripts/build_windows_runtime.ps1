param(
  [string]$EnvName = "ai-cad-runtime-win",
  [string]$OutFile = "packaging/runtime/windows/python-cadquery-runtime-win64.zip"
)

$ErrorActionPreference = "Stop"

Write-Host "Creating Windows runtime environment $EnvName"
conda env remove -n $EnvName -y 2>$null
conda env create -f environment.yml -n $EnvName
conda install -n $EnvName -c conda-forge conda-pack -y

New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
conda-pack -n $EnvName -o $OutFile

Write-Host "Runtime archive written to $OutFile"

