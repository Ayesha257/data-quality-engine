$files = @(
  @{ path="OneDrive_1_26-01-2026 - latest data set\Booked Orders.xlsx"; sheet="Booked Orders (New - YTD)" },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Classic Order Book (ZVEN034- SEI version)_2026-01-20 073112_5fa9fb45.xlsx"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Customer List.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Supplier List.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Stock Report.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Goods Receipt Report.xls"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Invoice List 1st January 2020 - 31st December 2025.xlsx"; sheet=$null },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Product Data by Product Site.xlsx"; sheet="Sheet1" },
  @{ path="OneDrive_1_26-01-2026 - latest data set\Open PO Orderbook.xlsx"; sheet=$null }
)
foreach ($f in $files) {
  Write-Host ""
  Write-Host "======================================================"
  Write-Host "RUNNING:" $f.path
  if ($f.sheet) {
    python main.py $f.path --sheet $f.sheet
  } else {
    python main.py $f.path
  }
}
