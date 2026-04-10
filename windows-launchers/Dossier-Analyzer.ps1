$folder = "C:\Users\mouli\Documents\Projects\Dossier-Analyzer"

if (Test-Path $folder) {
    Set-Location $folder

    git pull
    uv sync
    uv run streamlit run app.py
}
else {
    $parent = Split-Path $folder -Parent
    Write-Host "Project 'Dossier-Analyzer' should be in $parent"
}

# Wait until Enter is pressed
Read-Host "Press Enter to quit"