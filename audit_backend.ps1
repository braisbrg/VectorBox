# Audit Backend Dependencies
Write-Host "Running pip-audit..." -ForegroundColor Cyan
Set-Location backend
python -m pip_audit -r requirements.txt --strict
if ($LASTEXITCODE -eq 0) {
    Write-Host "Security Audit Passed!" -ForegroundColor Green
} else {
    Write-Host "Security Audit Failed!" -ForegroundColor Red
}
Set-Location ..
