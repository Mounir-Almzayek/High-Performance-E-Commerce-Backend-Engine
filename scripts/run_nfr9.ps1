<#
Run NFR9 stress test (PowerShell)
Usage:
  .\scripts\run_nfr9.ps1 -Host 'http://localhost' -Users 100 -SpawnRate 10 -RunTime '10m'
#>
param(
  [string]$TargetUrl = 'http://localhost',
  [int]$Users = 100,
  [int]$SpawnRate = 10,
  [string]$RunTime = '10m'
)

$OutDir = 'docs/benchmarks'
$OutPrefix = "nfr9-$Users-users"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

Write-Host "Starting NFR9 Locust run: host=$TargetUrl users=$Users spawn_rate=$SpawnRate run_time=$RunTime"

# Optionally start services: docker-compose up -d

# Prefer local locust binary if available, otherwise instruct to run via docker-compose
if (Get-Command locust -ErrorAction SilentlyContinue) {
  & locust -f tests/stress/locustfile.py --host $TargetUrl --users $Users --spawn-rate $SpawnRate --run-time $RunTime --headless --html "$OutDir\$OutPrefix.html" --csv "$OutDir\$OutPrefix"
  Write-Host "Locust finished. Reports: $OutDir\$OutPrefix.html and $OutDir\${OutPrefix}_stats.csv"
} else {
  Write-Host "Locust binary not found on PATH. To run inside Docker Compose, use:" -ForegroundColor Yellow
  Write-Host "  docker-compose run --rm locust locust -f tests/stress/locustfile.py --host $TargetUrl --users $Users --spawn-rate $SpawnRate --run-time $RunTime --headless --html /tmp/$OutPrefix.html --csv /tmp/$OutPrefix" -ForegroundColor Cyan
  Write-Host "Then copy the reports from the locust container to your host, for example:" -ForegroundColor Yellow
  Write-Host "  docker ps   # find the running container id" -ForegroundColor Cyan
  Write-Host "  docker cp <container>:/tmp/$OutPrefix.html docs/benchmarks/" -ForegroundColor Cyan
}
