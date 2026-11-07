# comfy-workflow-exe

把 ComfyUI 工作流 JSON 转成**可双击的本地独立 exe 程序**：进程内直调模型出图（零端口零服务），环境扫描有则复用无则补齐，qfluentwidgets Fluent UI，显存全自动适配，多分支=多 exe。

这是一个**标准 agent skill 仓库**：自包含、无机器路径、无用户隐私，clone 到任何支持 skill 约定的 agent 即可使用。

## 安装

```bash
# clone 到你的 agent 的 skills 目录（按所用 agent 的约定，常见位置示例）
git clone <本仓库> ~/.claude/skills/comfy-workflow-exe      # Claude Code
git clone <本仓库> ~/.agents/skills/comfy-workflow-exe      # 通用 agents 目录
# 或任意位置 clone 后软链进 skills 目录
ln -s /path/comfy-workflow-exe <你的skills目录>/comfy-workflow-exe
```

安装后对 agent 说「用 comfy-workflow-exe 把 <工作流.json> 转成 exe」即可触发。

## 仓库结构

```
comfy-workflow-exe/
├─ SKILL.md                 执行骨架（S-1 前置门 + S0–S6 流程 + 硬规则 + 架构要点）
├─ README.md                本文件
├─ references/
│   ├─ ui-style.md          Fluent 设计令牌 + 双列布局硬规格 + 单多选判定律 + pywin32 接线
│   └─ ux-flow.md           S-1 跑通引导 → 心智分析 → 弹窗询问 → UX 规划
├─ scripts/                 全参数化脚本（工作流无关，无机器默认值——路径全部显式传参）
│   ├─ s0_recon.py          侦察：节点清单 + venv 实测 INPUT_TYPES + 模型/显存线索
│   ├─ s1_gen_plan.py       契约：UI→API 对位 + Reroute 塌缩 + 分支冻结(--fix-*)
│   ├─ s3_new_program.py    骨架：程序根 + 快照闭包 + pyrunner + 通用UI + 依赖预装
│   ├─ s4_launcher.py       入口：PyInstaller --noconsole exe + start.bat + 桌面 lnk
│   ├─ s5_accept.py         验收：最低分辨率跑通 + VRAM 前后采样 + 零监听审计
│   └─ s6_ui_check.py       UI 自检五项：控件覆盖/默认值/键入clamp/visible_when联动/渲染截图
└─ templates/
    ├─ pyrunner/            通用推理基座 8 模块（bootstrap/executor/runner/vram/scan/cli…）
    ├─ ui_generic.py        通用 schema 驱动 Fluent UI（任何工作流开箱即用）
    └─ launcher.py          双击启动器（frozen 用 sys.argv[0] 定位，pythonw 无黑框）
```

## 快速使用（agent 视角）

0. **前置门**：引导用户确认工作流已在 ComfyUI 跑通（没跑通先按排查树解决）
1. `scripts/s0_recon.py --workflow <json> --comfyui <root> --out report.json`
2. 弹窗询问用户（AskUserQuestion）：先给完整「节点 id→字段→现值」候选表，再问保留哪些输入 + 分支落地（多分支=多 exe）
3. `scripts/s3_new_program.py --name <名> --workflow <json> --comfyui <root> --venv <py> --models <dir> --out-root <输出根> ...`
4. 撰写 params_schema / deps_manifest / ui_plan（AI 步，G1–G6 门槛）；分支冻结用
   `scripts/s1_gen_plan.py ... --fix-bypass <ids> --fix-activate <ids> --fix-value <n/f=v>`（回读断言）
5. `scripts/s4_launcher.py --program <dir> --venv <py>`（--noconsole 无黑框）
6. `scripts/s5_accept.py --program <dir> --venv <py> --params smoke.json` → 交付报告
7. `scripts/s6_ui_check.py --program <dir> --venv <py>` → UI 自检五项（交付前必做）

## CLI 契约（AI 接口）

生成程序的 CLI 是 AI 驱动的正式接口：**stdout = 机器输出（JSON），stderr = 人类日志**。

| 命令 | 用途 |
|------|------|
| `runner scan` | 环境三色报告（JSON）；exit 2=缺依赖 3=缺模型 |
| `runner schema` | 打印 params_schema（参数/类型/范围/枚举） |
| `runner describe` | **AI 首选入口**：一次给全 schema+defaults+config+scan+合法键清单+示例命令 |
| `runner run --params p.json [--json] [--progress]` | 出图；`--json`=stdout 纯 JSON（含错误/traceback）；默认带步进心跳 |
| `runner outputs --last N` | 产物清单（文件/实际 seed/时间，最新优先） |
| `runner serve` | 常驻推理服务（UI 子进程；协议 INIT→READY→PROGRESS→RESULT，支持 `CMD release_vram`） |

- 参数键 fail-fast：未知键立即报错并列出 `valid_keys`（校验在加载模型前，秒级）
- exit codes：`0` 成功｜`2` 缺依赖｜`3` 缺模型｜`4` 运行时/参数错误｜`5` 环境不可用
- 分支冻结在生成期（graph_plan --fix-*），CLI 无分支解冻后门

**AI 典型会话**：`describe` 认识程序 → 改 params → `run --json` → `outputs` 核对 → 交付。

母版设计规范（含实测坑清单）由安装者自行维护，路径写入 SKILL.md 顶部占位即可；本仓库不携带任何用户私有文档。

## 边界

- 不启服务/不开端口/不带 ComfyUI 前端；本机 ComfyUI 目录只读复用
- 运行时用所选 venv 的 Python+torch（方案①）；模型永不自动下载，缺失给清单+手选
- 禁 webview；UI 进程零 CUDA（推理在 serve 常驻子进程）
