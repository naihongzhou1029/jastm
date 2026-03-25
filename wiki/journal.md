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

---

# 2026-03-21 (Session 2)

## Feature: Warnings Column & Windows VAS Correction

### Task Overview
本次 Session 針對兩個獨立問題進行修正：一是彙整報告中「Flags」欄位語義不明確，二是發現 `psutil` 在 Windows 上回傳的 `vms` 欄位語義與 Linux 完全不同，導致碎片化偵測邏輯失效。

---

### Phase 1: Flags → Warnings 欄位重構

**問題**：`--aggregate-summaries` 的 Markdown 表格中有一個 `Flags` 欄位，內容為 `CPU_PEAKS` 或 `MEM_PEAKS`，但這些資訊已在獨立的計數欄位中體現，語義重複且對診斷無直接幫助。

**設計決策**：
- 將欄位重命名為 `Warnings`，專門用於標註需要關注的診斷警告。
- 廢除 `CPU_PEAKS` / `MEM_PEAKS` 標記，改為兩種具備實際診斷意義的警告：
  - `MEM_LEAK`：可用系統 RAM 呈持續下降趨勢（`mem_slope < 0` 且 `R² > 0.7`）。
  - `FRAG_RISK`：VMS / RSS 比值超過 1.5x，或 VMS 持續成長而 RSS 相對平坦。
- 欄位無警告時顯示 `-`，保持表格整潔。

**同步修正**：
- `RAM Slope` 欄位標題改為 `RAM Slope (MB/h)`，與相鄰的 `RAM(MB)` 欄位單位對齊，避免讀者對數值規模產生疑問。
- 更新 `test_4_17` 測試案例，驗證新的欄位名稱與警告邏輯。

---

### Phase 2: Windows VAS 語義修正

**問題發現**：實際執行 Notepad 監控後，CSV 中出現 `VMS_MB ≈ 3 MB`、`RSS_MB ≈ 14 MB` 的異常數據——VMS 竟小於 RSS，與「VMS 應大於等於 RSS」的基本認知矛盾。

**根本原因**：`psutil.memory_info()` 在不同平台的欄位語義不同：

| 平台 | `vms` | `rss` |
|------|-------|-------|
| Linux | 總虛擬地址空間（≥ RSS） | 常駐實體 RAM 頁面 |
| Windows | `PagefileUsage`（僅磁碟上的分頁，通常 < RSS） | Working Set（實體 RAM，含共享 DLL） |

Windows 的 `vms` 僅代表「已換出至磁碟的頁面」，對於記憶體幾乎全在 RAM 中的閒置程序（如 Notepad），此值自然遠小於 Working Set。

**解決方案**：
- 在 Windows 上改用 `mem_info.private`（Private Bytes），即程序自行提交的虛擬記憶體總量（不含共享 DLL），語義上等同於 Linux 的 `vms`。
- Linux / macOS 維持原有的 `mem_info.vms`，行為不變。
- `show_summary()` 在 Windows 上於段落標題加註 `(Windows: VMS=Private Bytes, RSS=Working Set)`，讓使用者清楚知道欄位定義。
- `CLAUDE.md` 補充平台差異說明，供未來維護參考。

**發現的關鍵事實**：`psutil.memory_info()` 在 Windows 上回傳的 namedtuple 包含遠多於 Linux 的欄位（`private`、`pagefile`、`peak_wset` 等），可通過 `mi._fields` 查詢所有可用欄位。

---

### Phase 3: 實機驗證

以 `notepad.exe` 為目標，執行多輪 1 分鐘監控並產生彙整報告：
- **修正前**：VMS ≈ 3 MB，RSS ≈ 14 MB，VMS/RSS ≈ 0.21x — 碎片化偵測完全失效。
- **修正後**：VMS（Private Bytes）與 RSS（Working Set）的比值回歸正常範圍，分析邏輯恢復有效。
- 所有 48 項煙霧測試全數通過，變更已推送至遠端倉庫。

---

### Insights & Observations
- **跨平台 API 語義陷阱**：相同的欄位名稱（`vms`）在不同作業系統上可能代表完全不同的概念。直接以欄位名稱假設語義，而不驗證平台行為，是跨平台工具中常見的隱性 Bug 來源。
- **用實際數據驗證假設**：此 Bug 並非靜態分析發現，而是實際執行後觀察到「VMS < RSS」的異常數值才觸發調查。實機測試對於驗證跨平台行為不可或缺。
- **欄位命名應反映語義**：`Flags` 到 `Warnings` 的重命名看似微小，但明確傳達了「此欄位用於標示需要人工關注的異常」，降低了讀者的認知負擔。

---

# 2026-03-25

## Release: v1.8

### Task Overview
發布 `jastm` v1.8 版本。此版本整合了多項功能增強與關鍵修正，顯著提升了在 Windows 平台上的監控能力。

### Phase 1: New Features & Enhancements
- **Windows 事件日誌收集 (`--events-report`)**：新增功能以收集最近 24 小時內的 Windows 系統與應用程式事件（Warning 以上等級），並產生 Markdown 格式報告。此功能利用 PowerShell `Get-WinEvent` 實現，無需額外依賴。
- **虛擬地址空間 (VAS) 監控**：支援追蹤 `VMS` 與 `RSS` 指標，並能自動偵測內存碎片化 (Fragmentation) 風險。
- **報告欄位重構**：將彙整報告中的 `Flags` 欄位更名為 `Warnings`，並新增 `MEM_LEAK` 與 `FRAG_RISK` 診斷邏輯；同時為 `RAM Slope` 標題補上單位 `(MB/h)`。
- **簡化輸出**：從實作與文件中全面移除不再使用的 `machine_id` 欄位，降低輸出冗餘。

### Phase 2: Bug Fixes
- **Windows VMS 數據修正**：將 Windows 平台上的 VMS 採樣點從 `PagefileUsage` 修正為 `Private Bytes`，確保與 Linux 平台的 VMS 語義一致，使碎片化分析邏輯在 Windows 上能正確運作。

### Phase 3: Documentation & Maintenance
- **README 同步**：更新 `README.md` 中的輸出範例與功能說明，反映最新的 CSV 結構與分析行為。
- **測試套件更新**：同步更新 `smoke_test.py` 與 `happy_path.py`，確保涵蓋所有新增參數與欄位變更。

### Phase 4: Finalization
- 執行所有測試案例並確認通過。
- 建立 Git Tag `v1.8`。

### Insights & Observations
- **工具定位的演進**：`jastm` 從單純的 CPU/RAM 監控工具，逐步演進為具備初步自動診斷能力（如碎片化偵測、事件日誌關聯）的 Soak Testing 輔助工具。
- **平台適配的重要性**：Windows 與 Linux 的系統調用差異（如 Memory Info 語義）是開發監控工具時最耗時但也最重要的部分。
