# 2026-03-21

## Feature: Virtual Address Space (VAS) Monitoring & Fragmentation Detection

### Task Overview
目標是為 `jastm` 增加監控特定進程虛擬地址空間 (VAS) 的功能，用以識別內存碎片化（Memory Fragmentation）或地址空間耗盡（Address Space Exhaustion）的風險。

### Phase 1: Requirement Analysis & Implementation Plan
- **核心指標**：追蹤 `VMS` (Virtual Memory Size) 與 `RSS` (Resident Set Size)。
- **偵測邏輯**：
    - 計算 `Fragmentation Gap` (VMS - RSS)。
    - 分析長期趨勢：若 VMS 斜率為正且持續增長，但 RSS 平緩，則視為碎片化風險。
    - 設定閥值：若 `VMS / RSS > 1.5x` 則觸發警報。
- **技術決策**：
    - 使用 `psutil` 獲取進程內存資訊。
    - 更新 CSV 格式，從 3 欄位擴展至 5 欄位（新增 `VMS_MB`, `RSS_MB`）。
    - 由於是長期壓力測試（Soak Test），分析邏輯應基於整個測試週期的線性回歸，而非短期的滑動窗口。

### Phase 2: Implementation Details
- **數學工具**：新增 `compute_linear_regression()` 函數，用於計算趨勢斜率與 R-squared。
- **DataCollector**：更新 `collect_metrics` 與 `write_log`，確保在監控特定 PID 時能正確記錄 VAS 數據；若為全系統監控則記錄為 `N/A`。
- **DataAnalyzer**：擴展 `load_data` 與 `show_summary`。在分析模式下，系統會自動比對 VMS 與 RSS 的增長速率，並在報告中顯示「FRAGMENTATION RISK DETECTED」警告。
- **Report Table**：在彙整報告 (`--aggregate-summaries`) 的 Markdown 表格中新增 `Flags` 欄位，標註異常狀態。

### Phase 3: Testing & Debugging Insight
- **發現 Bug**：現有的 `smoke_test.py` 在執行 `--program`（空程序）測試時會導致掛起。
- **原因診斷**：`jastm.py` 在進入互動式程序選擇模式時會等待 `input()`，但在非互動式環境（如自動化測試）中這會造成永久阻塞。
- **解決方案**：
    - 在 `jastm.py` 中加入 `sys.stdin.isatty()` 檢查，非互動環境直接退出。
    - 在測試框架中使用 `stdin=subprocess.DEVNULL` 確保輸入流立即結束。
- **相容性修正**：更新測試案例以適應新的 5 欄位 CSV 結構，並處理 Markdown 報表中因 `<br>` 標籤導致的字符串匹配失敗。

### Phase 4: Finalization
- 更新 `README.md`，同步最新的 CSV 格式與 VAS 分析行為說明。
- 驗證所有 53 項測試通過，並將變更推送到遠端倉庫。

### Insights & Observations
- **碎片化指標的重要性**：在長期運行的穩定性測試中，單看 RSS (Working Set) 可能會誤以為內存使用穩定，但 VMS 的持續膨脹往往預示著內存分配器（Allocator）的碎片化問題或虛擬內存洩漏。
- **強健的測試環境**：自動化測試應始終考慮「非互動性」，避免代碼在無人值守環境下進入等待狀態。
