Write-Host "🗄️  Starting VectorBox backup..." -ForegroundColor Cyan
docker-compose exec -T backend python scripts/backup_manager.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Backup failed"
    exit 1
}
Write-Host ""
Write-Host "📦 Latest backups:" -ForegroundColor Cyan
Get-ChildItem ./backups/vectorbox_backup_*.zip -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 |
    Format-Table Name, @{
        Name="Size (MB)"
        Expression={[math]::Round($_.Length/1MB, 2)}
    }
