[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host "Обновление расписания группы 126-2 ТУСУР..." -ForegroundColor Cyan

$parserProject = "C:\Users\User\.gemini\antigravity\scratch\ParserTest"
if (Test-Path $parserProject) {
    dotnet run --project $parserProject
    Write-Host "Готово! Расписание обновлено в schedule.ics и schedule.json." -ForegroundColor Green
} else {
    python parser.py --all-weeks
}
