$ErrorActionPreference = "Stop"

Write-Host "Triggering 'Popular on Letterboxd' update..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/tools/update-popular"
    Write-Host "Success: $($response.message)" -ForegroundColor Green
}
catch {
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host "Make sure the backend is running on http://localhost:8000" -ForegroundColor Gray
}
