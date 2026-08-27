# ---------------------------------------------------------------------------
# sync.ps1  -  send whatever changed up to GitHub, in one command.
#
# WHY THIS EXISTS
#   Claude can write files straight into this folder, but it cannot push to
#   GitHub - pushing needs your credentials, which only Windows has. So the
#   push stays yours. This makes it one short command instead of three, with
#   nothing to remember and nothing to type wrong.
#
# HOW TO USE
#   .\sync.ps1                      -> commits with an automatic message
#   .\sync.ps1 "fixed the tags"     -> commits with your own message
#
# It refuses to run if a secret somehow ended up staged, as a last safety net.
# ---------------------------------------------------------------------------

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Checking what changed ===" -ForegroundColor Cyan

git add -A

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing changed. Nothing to send." -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

$staged | ForEach-Object { Write-Host "   $_" }

# --- safety net: never let a real secret leave this machine ----------------
$danger = $staged | Where-Object {
    $_ -match '(^|/)\.env$' -or
    $_ -match '\.json$'     -or
    $_ -match 'secrets\.toml$'
}
if ($danger) {
    Write-Host ""
    Write-Host "STOPPED - these look like secret files:" -ForegroundColor Red
    $danger | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    Write-Host "Nothing was sent. Check .gitignore before trying again." -ForegroundColor Red
    Write-Host ""
    git reset | Out-Null
    exit 1
}

if (-not $Message) {
    $count = ($staged | Measure-Object).Count
    $Message = "Update $count file(s) - $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}

Write-Host ""
Write-Host "=== Saving ===" -ForegroundColor Cyan
git commit -m $Message

Write-Host ""
Write-Host "=== Sending to GitHub ===" -ForegroundColor Cyan
git push

Write-Host ""
Write-Host "Done. GitHub has it." -ForegroundColor Green
Write-Host ""
