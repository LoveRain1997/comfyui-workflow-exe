---
name: comfy-workflow-exe
description: 把 ComfyUI 工作流 JSON 转成可双击的本地独立 exe 程序（进程内直调模型出图，零端口零服务；通用 pyrunner 推理基座 + qfluentwidgets Fluent UI；显存功能全自动适配；多分支=多 exe）。触发词：工作流转exe、工作流打包、独立出图程序、comfy workflow to exe。
---

# 工作流 JSON → 独立 EXE

母版规范（权威）：`<用户 git 仓库>/SKILL设计.md`（Rev 制）。本 SKILL 只含**通用**内容——用户隐私路径、具体工作流细节一律不入本目录。

## 0. 工作原则

- **Token 免费，不用节约**：分析全面、方案完整、不省步骤不省说明。
- **用户意图一句话，exe 就合理**：用户说「这个工作流我只输入文字，你输出图片」→ 界面只暴露文字输入 + 出图输出，其余全部内部默认值。
- **先交互与用户心智，后技术实现**；显存是通用功能，全自动适配，**不询问用户**。
- **多分支 = 多 exe**（简单原则）：工作流有几条执行路径就生成几个 exe，每个 exe 只含自己路径的参数，**不做流程分支切换 UI**（不做 switch_map、不做模式段）。

## 输出位置契约

- 每个分支一个程序根：`D:\ComfyExe\<程序名>\` 或多分支时 `D:\ComfyExe\<程序名>_<分支名>\`
- 双击入口：`<程序根>\<程序名>.exe`（PyInstaller launcher）+ `start.bat` 备选 + 桌面 `<程序名>.lnk`
- 程序自包含：`pyrunner/`（通用推理基座）+ `<工作流名>_ui.py`（按 ui_plan 生成）+ engine_snapshot/ + pylibs/ + plugins/ + config.json + 三份契约 JSON

## 通用 py 基础脚本设计（templates/pyrunner/，工作流无关）

| 模块 | 职责 | 通用性要点 |
|------|------|-----------|
| `bootstrap.py` | 配置加载、路径接线（folder_paths 多根）、快照核心 + 官方 extra 初始化、插件包导入（manifest 驱动） | 不含任何具体包名硬编码； REQUIRED_PACKS 仅兜底 |
| `executor.py` | 迷你图执行器：需求驱动执行（只从 OUTPUT_NODE 递归）、懒开关、旁路透传、输出缓存（模型常驻）、`torch.no_grad` | 工作流无关，吃 graph_plan.json |
| `runner.py` | params 注入 → 执行 → 产物收集 + sidecar；`release_vram()`/`vram_free_mb()` 通用显存接口 | 按 schema 绑定写值，工作流无关 |
| `vram.py` | 显存策略：`apply_mode`（按 manifest vram_sync_nodes 覆写）、`after_run`、`vram_free_mb()` | 通用 |
| `scan.py` | 环境三色报告（✅复用/⬇可补齐/⚠optional/❌需手选）；模型多根递归 | manifest 加 `optional: true` 的模型缺失仅警告 |
| `cli.py` | `scan`/`schema`/`run`/`serve`；serve 协议 = `INIT → READY → PROGRESS → RESULT`（stdin 每行 params JSON，支持 `CMD release_vram`）；入口 reconfigure UTF-8（坑34） | 协议通用，UI 框架无关 |

**UI 模块不在 pyrunner 里**：S3 默认装 `templates/ui_generic.py`（schema 驱动，任何工作流开箱即用）；per-flow 精修版用 `--ui` 覆盖（参照 `templates/reference_ui_moss.py`）。

## 仓库安装

本 skill 是自包含 git 仓库：`git clone <repo> ~/.zcode/skills/comfy-workflow-exe`（或软链入 skills 目录）即可安装，详见 README.md。结构 = SKILL.md + references/（2 规范）+ scripts/（5 参数化脚本）+ templates/（pyrunner 基座 + ui_generic + per-flow 参照 + launcher）。

## 执行流程（S0–S6）

| 段 | 动作 | 工具 |
|----|------|------|
| S0 侦察 | 工作流节点清单；venv 实测各包 INPUT_TYPES；path_map：枚举执行路径（分叉开关→节点→独有参数→资源占用）；环境模拟三件套 | `python scripts/s0_recon.py --workflow <json> --comfyui <root> --out report.json` |
| S3 骨架 | 程序根 + 快照闭包 + pyrunner 模板 + PySide6/qfluentwidgets/pywin32 预装（pywin32 路径修复见 ui-style.md §0）+ config.json（`disable_async_offload: true`） | `python scripts/s3_new_program.py --name <名> --workflow <json> --comfyui <root> --venv <py> --models <dir> [--models <dir2>] --input <dir>` |
| S1A 心智分析 | 四问：用户是谁/心动线/哪个参数在惩罚用户/状态恢复 | AI 步骤 |
| S1B **弹窗询问** | **AskUserQuestion 问用户「哪些输入要保留」**：以工作流节点 id 为锚（展示 node_id+字段+现值），分组给推荐项（提示词类/尺寸类/数量种子/模型选择类），用户多选；未选中一律进「高级」折叠；另问 exe 命名与分支拆分确认。**显存相关一律不问**（全自动）。用户未响应 → 声明按推荐执行 | AI 步骤（模板见 ux-flow.md §2） |
| S1C UX 规划 | 按 ui-style.md 布局规范出 ui_plan：双列布局/参数行卡/大预览/操作条 | AI 步骤 |
| S1D 契约 | 机械对位（`s1_gen_plan.py`，多分支用 `--fix-bypass/--fix-activate` 冻结本分支）+ AI 撰写 params_schema / deps_manifest / ui_plan | `python scripts/s1_gen_plan.py --workflow <json> --program <dir> --comfyui <root> [--fix-bypass 66 --fix-activate 70]` |
| S4 入口 | PyInstaller **--noconsole**（无黑框，坑37）launcher（frozen 用 sys.argv[0] 定位，坑26）+ start.bat + 桌面 lnk；多分支逐个构建 | `python scripts/s4_launcher.py --program <dir> --venv <py> [--name <exe名>]` |
| S5 验收 | 最低分辨率跑通（R1）→ R2 零监听 → R6 显存回落 → UI 冒烟（serve 握手/窗口存活） | `python scripts/s5_accept.py --program <dir> --venv <py> --params smoke.json` |
| S6 交付 | 报告产物树 + 验收证据 + 已知限制；坑沉入母版 | — |

## 契约硬规则（G1–G6）

1. **覆盖律**：工作流每个可调输入必须 core/advanced/internal 三选一恰好归档一次（G1）。core 成员 = **用户弹窗选中项**，不再是 AI 单方判定。
2. core ≤ 10；超了合组（模型组、预设组各算 1）。
3. **单选/多选判定律**（ui-style.md §4）：「A 和 B 能同时为真吗」能=多选，不能=单选。
4. **功能矛盾矩阵**（conflict_matrix）随 params_schema 产出并给用户过目（ui-style.md §5）。
5. **vram_sync_nodes**：类名/字段名命中 卸载|clean|free|unload|offload|内存|显存|GC 即归入；执行期自动覆写，UI 文案「由显存勾选接管」。
6. **internal 必附一句理由**（G4）；分支冻结后不再属于本 exe 的节点 → 直接移出 plan（不是 internal）。

## 显存通用功能（自动，不询问）

- 运行条常驻：**「完成后释放显存」勾选**（勾=跑完统一卸载并清缓存；不勾=模型常驻连出更快）+ **「立即释放显存」独立按钮**（`CMD release_vram`，只卸载权重不清缓存）+ 显存空闲值常驻状态行
- bootstrap 自动：空闲 <6GB → lowvram（坑25）；`disable_async_offload: true` 默认开（坑30）
- 图内卸载类节点由 manifest vram_sync_nodes 执行期覆写（不双清不漏清，坑17）

## 架构要点（防闪退，实测沉淀）

- **UI 与推理分进程**（坑31）：UI（qfluentwidgets）零 CUDA；serve 常驻子进程；常驻读取线程单例分发回传行；子进程崩溃只报失败不陪葬窗口
- serve 握手：INIT（schema/defaults/config/scan）→ READY；握手前跳过子进程 bootstrap 日志
- pythonw 子进程 stdio 回退 GBK（坑34）→ cli 入口 reconfigure + Popen 传 UTF-8 环境
- pythonw 下 UI 异常静默 → 调试先用控制台 python（坑27）
- pywin32 --target 路径修复（坑32）；二次 basicConfig no-op（坑33）

## 已知限制（v1）

- ui.py 按 ui_plan 逐工作流生成（参照实现 reference_ui_moss.py）；ServeBridge/serve 协议层通用
- 插件补齐：manifest 无 git_url 的包标记 manual
- 多分支多 exe 的目录隔离会重复占用快照/依赖磁盘（每分支约 700MB），共享化留 §A
