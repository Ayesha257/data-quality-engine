# Batch runner for the full Phase 1 pipeline (Tasks 1-6).
# Edit paths below to match files on your machine, then:
#   .\run_all_task1_task2.ps1
#
# Tip for demos: start with Customer List / Supplier List (small),
# not Goods Receipt Report (~87MB).

$files = @(
  @{ path="OneDrive_1_26-01-2026 - latest data set\Customer List.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Supplier List.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Booked Orders copy.csv"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Stock Report.xls"; sheet=$null }
  # Uncomment larger files once the small ones look good:
  # @{ path="OneDrive_1_26-01-2026 - latest data set\Goods Receipt Report.xls"; sheet=$null }
)

$python = if (Test-Path ".\venv\Scripts\python.exe") { ".\venv\Scripts\python.exe" } else { "python" }

foreach ($f in $files) {
  if (-not (Test-Path $f.path)) {
    Write-Host "SKIP (missing):" $f.path -ForegroundColor Yellow
    continue
  }
  Write-Host ""
  Write-Host "======================================================"
  Write-Host "RUNNING:" $f.path
  if ($f.sheet) {
    & $python main.py $f.path --sheet $f.sheet
  } else {
    & $python main.py $f.path
  }
}
