# comfy-workflow-exe

把 ComfyUI 工作流 JSON 转成**可双击的本地独立 exe 程序**：进程内直调模型出图（零端口零服务），环境扫描有则复用无则补齐，qfluentwidgets Fluent UI，显存全自动适配，多分支=多 exe。

## 安装

```bash
# 方式一：直接放入 skills 目录（ZCode/类 ClaudeCode agent）
git clone <本仓库> ~/.zcode/skills/comfy-workflow-exe

# 方式二：任意位置 clone 后软链
git clone <本仓库> /path/comfy-workflow-exe
ln -s /path/comfy-workflow-exe ~/.zcode/skills/comfy-workflow-exe
```

安装后对 agent 说「用 comfy-workflow-exe 把 <工作流.json> 转成 exe」即可触发。

## 仓库结构

```
comfy-workflow-exe/
├─ SKILL.md                 执行骨架（S0–S6 流程 + 硬规则 + 架构要点）
├─ README.md                本文件
├─ references/
│   ├─ ui-style.md          Fluent 设计令牌 + 双列布局硬规格 + 单多选判定律 + pywin32 接线
│   └─ ux-flow.md           心智分析 → 弹窗询问 → UX 规划（ui_plan 确认前不写 UI）
├─ scripts/                 全参数化脚本（工作流无关）
│   ├─ s0_recon.py          侦察：节点清单 + venv 实测 INPUT_TYPES + 模型/显存线索
│   ├─ s1_gen_plan.py       契约：UI→API 对位 + Reroute 塌缩 + 分支冻结(--fix-*)
│   ├─ s3_new_program.py    骨架：程序根 + 快照闭包 + pyrunner + 默认通用UI + 依赖预装
│   ├─ s4_launcher.py       入口：PyInstaller --noconsole exe + start.bat + 桌面 lnk
│   └─ s5_accept.py         验收：最低分辨率跑通 + VRAM 前后采样 + 零监听审计
└─ templates/
    ├─ pyrunner/            通用推理基座 8 模块（bootstrap/executor/runner/vram/scan/cli…）
    ├─ ui_generic.py        通用 schema 驱动 Fluent UI（任何工作流开箱即用）
    ├─ reference_ui_moss.py per-flow 精修参照实现（含 switch_map 联动范例）
    └─ launcher.py          双击启动器（frozen 用 sys.argv[0] 定位，pythonw 无黑框）
```

## 快速使用（agent 视角）

1. `scripts/s0_recon.py --workflow <json> --comfyui <root> --out report.json`
2. 弹窗询问用户（AskUserQuestion）：保留哪些输入（节点 id 为锚）+ 分支落地（多分支=多 exe）
3. `scripts/s3_new_program.py --name <名> --workflow <json> --comfyui <root> --venv <py> --models <dir> ...`
4. 撰写 params_schema / deps_manifest / ui_plan（AI 步，G1–G6 门槛）；分支冻结用
   `scripts/s1_gen_plan.py ... --fix-bypass <ids> --fix-activate <ids> --fix-value <n/f=v>`
5. `scripts/s4_launcher.py --program <dir> --venv <py>`（--noconsole 无黑框）
6. `scripts/s5_accept.py --program <dir> --venv <py> --params smoke.json` → 交付报告

母版规范（权威，含 38 条实测坑清单）：见 SKILL.md 顶部指向的设计文档。

## 边界

- 不启服务/不开端口/不带 ComfyUI 前端；本机 ComfyUI 目录只读复用
- 运行时用所选 venv 的 Python+torch（方案①）；模型永不自动下载，缺失给清单+手选
- 禁 webview；UI 进程零 CUDA（推理在 serve 常驻子进程）
