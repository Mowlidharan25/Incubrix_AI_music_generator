# One-command test runner (Integration Standard #1)
$ErrorActionPreference = "Stop"
Write-Host "Running all tests with zero API keys..." -ForegroundColor Cyan
$env:PYTHONPATH = "backend"
& ".\backend\.venv\Scripts\python.exe" run_tests.py
