@echo off
powershell -NoExit -ExecutionPolicy Bypass -Command ^
"$folder = 'C:\Users\mouli\OneDrive\Bureau\Dossier-Analyzer'; ^
if (Test-Path $folder) { ^
    Set-Location $folder; ^
    git pull; ^
    uv sync; ^
    uv run streamlit run app.py; ^
} else { ^
    $parent = Split-Path $folder -Parent; ^
    Write-Host \"Project 'Dossier-Analyzer' should be in $parent\"; ^
}; ^
Read-Host 'Press Enter to quit'"