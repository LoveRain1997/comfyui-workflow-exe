# comfy-workflow-exe

**把 ComfyUI 工作流 JSON 转成双击即用的本地 exe**——AI Agent skill，全流程代码生成，无需手动搭界面。

![介绍视频](media/intro.mp4)

*30 秒看懂：工作流 JSON 进，双击 exe 出。字幕文件 [media/intro.srt](media/intro.srt)。*

## 它解决什么问题

**痛点**：工作流调好了，但每次出图都要启动 ComfyUI——界面、服务、模型缓存全部常驻，内存显存一直被占。

**方案**：skill 把工作流转成一个独立 exe。跑图时**不再启动 ComfyUI 的界面和服务**，进程内直调模型，用完即释。

- **ComfyUI 仍然需要**：模型、插件、venv 环境、引擎快照全部**原位复用**，装一次就够——省的是每次跑图都要启动本体的开销
- **用户只看到**：文字输入 + 出图，其余全自动（参数 AI 弹窗确认，不翻节点）
- **显存自动管理**：跑完自动释放（实测空闲 14.9GB），空闲不足自动降档
- **多分支 = 多 exe**：工作流几条路径就生成几个程序，互不干扰

## 工作方式（AI 驱动，7 段流程）

| 段 | 内容 |
|----|------|
| S-1 | **前置门**：工作流必须先在 ComfyUI 跑通（引导确认，不做带病转换） |
| S0 | 侦察：节点清单、插件实测、path_map（含默认档标记） |
| S1B | **弹窗询问**：完整节点 id 候选表 → 你选核心区保留哪些输入 |
| S3 | 骨架：程序根 + 引擎快照 + pyrunner 基座 + 依赖预装 |
| S1D | 契约：UI→API 对位 + 分支冻结（回读断言）+ G1–G6 门槛 |
| S4 | 无黑框 exe + 桌面快捷方式 |
| S5 | 验收：最低分辨率跑通 + 显存回落 + 零监听审计 + **UI 自检五项** |

全流程规则见 [SKILL.md](SKILL.md)：S-1 跑通门、开关默认原则（分支冻结跟随工作流默认档）、单选/多选判定律、功能矛盾矩阵、44 条实测坑清单。

## 安装

```bash
# clone 到你的 agent 的 skills 目录（按所用 agent 约定）
git clone <本仓库> ~/.claude/skills/comfy-workflow-exe
```

对 agent 说「**用 comfy-workflow-exe 把 <工作流.json> 转成 exe**」即触发。

## CLI 契约（生成程序的 AI 接口）

**stdout = 机器输出（JSON），stderr = 人类日志。**

| 命令 | 用途 |
|------|------|
| `runner describe` | **AI 首选**：schema+defaults+config+scan+合法键清单 一次给全 |
| `runner run --params p.json --json` | 出图（纯净 JSON，含错误/traceback） |
| `runner outputs --last N` | 产物清单（文件/实际 seed） |
| `runner scan` / `schema` | 环境 / 参数契约 |
| `runner serve` | UI 子进程协议（INIT→READY→PROGRESS→RESULT） |

参数键 fail-fast：未知键秒级报错并列出全部合法键。exit codes：0 成功｜2 缺依赖｜3 缺模型｜4 运行时错误｜5 环境不可用。

## 边界

- 不启服务/不开端口/不带 ComfyUI 前端；本机 ComfyUI 目录只读复用
- 运行时用所选 venv 的 Python+torch；模型永不自动下载，缺失给清单+手选
- 禁 webview；UI 进程零 CUDA（推理在 serve 常驻子进程）
- 工作流需先在 ComfyUI 跑通（S-1 前置门）
