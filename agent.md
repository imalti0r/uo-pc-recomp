# Agent State Log — uo-recomp

## 🗂️ Project
- **Path**: E:\Folders\Opencode\uo-recomp/
- **Theme**: 圣兽之王 (Unicorn Overlord, Switch ARM64) 静态重编译移植到 Windows + 安装器
- **Created**: 2026-11-13
- **Baseline**: robocopy 自 ffrs-recomp（TotK Recomp 路线），runtime/recomp/tools 同架构

## 🎯 Current Goal
阶段 3：**已突破标题画面，进入游戏实际内容**（雨夜庭院开场剧情，画面完全正确）。
崩溃已修、黑屏已修。当前推进「开场剧情 → 实际可操作战斗」。

## 📋 TodoList
- [x] Task 1: 项目定位 + 骨架（robocopy 自 ffrs-recomp） | Done
- [x] Task 2: 解包 XCI（title 010054B01AD92000，1.05） | Done
- [x] Task 3: 全量 lift（main 31638 / sdk 40375 函数，0 unsupported） | Done
- [x] Task 4: 首次 --boot 一次通过 | Done
- [x] Task 5: 画面全对（修了 NVN QUADS 索引绘制缺角） | Done
- [x] Task 6: 应用内存池 / SetHeapSize / AllocateMemoryBlock 全部成功 | Done
- [x] Task 7: 按 A 进 New Game 崩溃 → 已修（StandardAllocator 64 KiB 对齐） | Done
- [x] Task 8: 加载画面黑屏 → 已修（着色器翻译失败负缓存） | Done
- [x] Task 9: 开场剧情正常播放 | Done
- [ ] Task 10: 推进到可操作战斗（输入脚本 / 对话跳过）| **In Progress**
- [x] Task 10b: 2160p/1800p/1440p 输出 + DLSS 支持 | Done
- [x] Task 10c: 帧率修复：每 op 的 cfg 查找 | Done（战场 21.6 → 58.6 fps）
- [ ] Task 11: 音频完整性
- [ ] Task 12: 安装器（uo_setup + Launcher，形态对齐 Engage-Launcher）
- [ ] Task 13: 完整可游玩验收（截图 / 模拟输入 / 日志）

## 🗂️ Current Log

### 2026-11-15 分辨率与帧率
- **分辨率**：UO 自身固定 1080p 渲染（游戏内多处尺寸源，只补丁两处会白屏/错位），改为 present 链放大：
  `UO_OUTPUT_RES` 1280x720..3840x2160，`UO_UPSCALER=fsr|bilinear|dlss`，`UO_DLSS_MODE=auto|dlaa|quality|balanced|performance|ultra`。
  `auto` 按输出尺寸选精确比例（1440p→Quality x1.5=2880x1620，1800p→Balanced x1.72=3310x1862，2160p→Performance x2=3840x2160）。
  实装：runtime/resolution.c 的 uo_apply/res_uo_sizes、vk_dlss.cpp 支持 game_id 3 + mode_auto、ui/menu.cpp 的行、uo_setup/launcher.py 的 output_res 行。
  DLSS 需要 upscalers/streamline/ 下的 4 个 DLL（sl.interposer/sl.common/sl.dlss/nvngx_dlss）。
- **帧率**：战场 3500 draws/帧时 replay 70ms、帧时 100ms（10 fps）。根因 = `runtime/vk_exec.c` 的 walk 对**每个 op** 调
  `cfg_bool("UO_DRAW_DIAG")`，而 cfg_bool 每次规范化键名并线性扫整张设置表。改为缓存的 `vk_exec_draw_diag()`，并把
  `UO_VP_DIAG`/`UO_RT_DIAG` 同样缓存成 vp_diag()/rt_diag()。replay 70ms → 3.0ms，战场 21.6 → 58.6 fps。
- **诊断工具**：`UO_REPLAY_TIME=1`（环境变量）打印每帧 replay 分阶段耗时（mirror/usnap/walk/ops）。
- **截图工具**：`UO_PRESENT_SHOT=<path>` + `UO_PRESENT_SHOT_FRAME=<n>` 截交换链（含 DLSS/FSR 输出），`--shot` 只能截游戏纹理。


### 2026-11-14 Task 7 修复：StandardAllocator 64 KiB 对齐
- **根因**：游戏请求 NVN pool 存储时用 `mov w2, #0x10000`（64 KiB 对齐），但它的
  `nn::mem::StandardAllocator` 只按 16 字节对齐，实测返回 `0x1e65cf000`（偏移 0xf000）。
  arena 建在这个地址上、按字节切分，三次分配 `0x2000000 + 0x4000000 + 0x2000000 = 0x8000000`
  正好吃满 128 MiB pool，被 0x7000 填充一挤，第三次失败返回 0 → 游戏用 base=0 → 崩。
- **修复**（`runtime/ov_system.c` `ov_std_alloc`，HOST_OBSERVER 注册
  `_ZN2nn3mem17StandardAllocator8AllocateEm`）：尺寸 ≥ 1 MiB 时多要 64 KiB，返回地址上取整到
  64 KiB。实测 6 个 pool 全部对齐，画面正常、不再崩。
- **关键教训**：不能改 pool 的 `mem`/`size`（`nvnMemoryPoolInitialize`），否则 GPU 读的页与
  CPU 写的页错开，画面全白。只能让**分配器本身**给出对齐地址。

### 2026-11-14 Task 8 修复：着色器翻译失败负缓存
- **根因**：翻译失败的着色器不缓存，每次 draw 都重试。加载画面 60 秒内 114 万次失败，
  帧率掉到 0.5 fps，看起来像黑屏卡死。
- **修复**（`runtime/gfx_shader.c`）：新增 4096 项失败哈希表 `failed_set`，
  `translate_one` 开头 `failed_has()` 直接返回 NULL，失败时 `failed_add()`。
  帧率恢复 57 fps。
- 附带：`UO_SHADER_DUMP=1` 保留失败着色器的 `.code` 文件便于分析。

## 🔑 已知遗留问题

### 剩余着色器翻译失败（不阻塞）
- `ArgumentException: Invalid shader stage "Count"` / `"15"`：
  SPH（0x50 字节着色器程序头）不在 `code+0x30` 处，或第 10-13 位不是合法 stage。
- `ArgumentOutOfRangeException`：同类问题。
- 加载画面不再卡死，但部分特效可能缺失。优先级低。

### 渲染 pass 的 draws 全为 0（诊断线索）
- `UO_DUMP` 抓帧显示所有 pass `draws 0`，但这是**加载画面/过场**的正常状态
  （游戏在播影片或等 IO），不是 bug。

## 📜 Archive

- **Phase 1（骨架）**：robocopy ffrs-recomp，.venv/.venv-py314/toolchain 用 junction 复用。
- **Phase 2（解包 + lift）**：XCI 3.29 GB，title 010054B01AD92000，main 11.6 MB / sdk 6.2 MB，
  无 subsdk0。全量 lift 0 unsupported，37 秒。首次 `--boot` 一次通过。
- **Phase 3（渲染）**：修 NVN QUADS 索引绘制缺角（`vk_exec_draw.c` op_draw_idx 重写成两个三角形）。
  标题画面 / 菜单 / 王座厅 / 城堡全景过场全部正确，58 fps。

## 📦 输入与产物

### 源材料（E:\Folders\Download\Baidu\）
- prod.keys / title.keys
- `[XCI][유니콘 오버로드][1.05][1G+1U+2D][中文][18.0.1].xci`（3.29 GB）

### 解包结果（build/extracted/uo-v105/）
- title **010054B01AD92000**，update 010054B01AD92800 v262144 (=1.05)，DLC …30001/2（空）
- display version 1.05，name 유니콘 오버로드
- 模块：main 11,662,381 B（build id 9c3116f0333ea157…）、sdk 6,217,525（12f161039300861f…）、rtld 7,952
- main.npdm：prio 44、core 0、stack 1 MiB
- RomFS 5 文件 / 3425.3 MB，全是 CriWare CPK：
  `Unicorn.CPK` 3.08 GB、`UnicornPatch.CPK` 496 MB、`Unicorn_{CN,KO,TW}.CPK` 各 ~4.6 MB
- 引擎：Vanillaware 自研（非 Unity/UE），数据走 CPK

### 构建与运行
- 构建：`.venv\Scripts\python tools\build_uo.py --jobs 8` → `build/uo-v105/uo.exe`
- 运行：`cd build\uo-v105; .\uo.exe --seconds N --shot shots\x.png`
- 输入脚本：`UO_INPUT_SCRIPT=tools/input/uo_title.txt`（格式：`<帧> <按键> <持续帧>`）
- 抓帧：`UO_DUMP=<frame>` → `dump/passes.txt` + `pass_*.png`
- 关键开关：`UO_POOL_ALIGN`（1=连存储一起移，会白屏，仅对比用）、
  `UO_POOL_ADDR_ALIGN`（默认 1，只对齐回报地址）、`UO_SHADER_DUMP`

## 🚀 Release v0.1.0-alpha.1 (2026-11-14)

- Repo: https://github.com/imalti0r/uo-pc-recomp
- Release: https://github.com/imalti0r/uo-pc-recomp/releases/tag/v0.1.0-alpha.1
- Asset: UO-Setup-0.1.0-alpha.1-windows.zip (166 MiB, sha256 35a9aca738e64c7e57b6dec5cadd2af77bf7d1ebb4c06f005a8fe4284ac8f49c)
- E2E verified: clean setup package installed to build/e2e-final in 120 s (result ok), produced a uo.exe that reaches the title screen.
- Icon: runtime/uo.ico, made by tools/make_icon.py from the PlayStation key art (rounded square, 10 sizes).
- Setup build: .venv-py314\\Scripts\\python.exe tools/make_engage_release.py --out build/release/uo-setup --zip build/release/UO-Setup-<ver>-windows.zip
- Open issue for the next build: the Vanillaware logo background plate draws as a visible rectangle (game draws a +/-550x430 quad; the atlas region has partial alpha and the draw is additive).
