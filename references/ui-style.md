# UI 风格规范：Fluent Design（QFluentWidgets / WinUI3 系）

> 来源：微软 Fluent Design 语言——toddming/QFluentExample → QFluentKit → PyQt-Fluent-Widgets 同源。
> 载体：**PySide6 + PySide6-Fluent-Widgets（qfluentwidgets）**，风格与 QFluentExample 一致。
> 禁令：**禁止 webview**（用户明令）；UI 进程零 CUDA（坑31：Qt+CUDA 同进程必崩 → serve 子进程）。

## 0. 环境接线（--target 安装必踩，实测沉淀）

`pip install --target <pylibs> PySide6 PySide6-Fluent-Widgets pywin32` 后必须补：
```python
sys.path.insert(0, PYLIBS)
for sub in ('win32', os.path.join('win32', 'lib'), 'win32com', 'Pythonwin'):
    sys.path.insert(0, os.path.join(PYLIBS, sub))     # pywin32 的 .pth 在非 site 目录不执行
os.add_dll_directory(os.path.join(PYLIBS, 'pywin32_system32'))  # pywintypes313.dll
```
连带的 numpy 若进 pylibs 必删（防污染 venv 的 llama-cpp 依赖）。

## 1. 设计令牌（qfluentwidgets 已内置实现，此处为语义约定）

- 主题：`setTheme(Theme.DARK)`（深色默认）；主题色 `setThemeColor('#60CDFF')`（深色）/ `#0078D4`（浅色）
- 令牌由 qfluentwidgets 主题系统给出（卡片/描边/文本三档色自动适配深浅色），禁止硬编码绕开
- 圆角：控件 4px、卡片 8px（库默认已符合）
- 字体：Segoe UI Variable / Microsoft YaHei UI；Subtitle 20px·600 / Body 14px / Caption 12px
- 图标：`FluentIcon` 枚举（Segoe Fluent Icons 同源）

## 2. 控件词汇表（qfluentwidgets 原生）

| 控件 | 用途约定 |
|------|----------|
| FluentWindow + addSubInterface | 导航双页（生成 / 设置） |
| SegmentedWidget | **单选**档位切（≤5 档），路径切换专用 |
| ComboBox | **单选**，>5 项 |
| CheckBox 组 | **多选**（可叠加项：LoRA 启用集合等） |
| SwitchButton | 布尔开关 |
| PrimaryPushButton | 每屏最多一个（=生成） |
| PushButton / ToolButton | 次级操作 |
| SimpleCardWidget / CardWidget | 参数分组容器（Card-HeaderCard 心智） |
| TextEdit / LineEdit / SpinBox / DoubleSpinBox / Slider | 输入族 |
| **可编辑 ComboBox（presets）** | **特殊数值参数**：深入插件源码后给预设档位（如百万像素 [0.5,1.0,1.5,2.0,2.5,3.0]）+ 可自由键入；schema 字段 `presets`，值解析 float，非法输入回落默认 |
| ProgressBar / ProgressRing | 步进进度 / 忙态 |
| InfoBar.success / error（TOP_RIGHT） | 反馈，禁 alert() |
| ImageLabel | 缩略图（参考图 / 最近出图） |
| SmoothScrollArea | 页面滚动 / **高级区局部滚动（§3.4）** |

## 3. 布局模板（可复用）：图像输出型双列布局

> 所有「输入参数 → 出图」类 exe 统一用此模板；宽度/高度/字号是硬规格，禁止随意压缩输入区。

```
┌──────────────────────────────────────────────────────────────────┐
│ 顶部卡（全宽，紧凑）：路径/模式 SegmentedWidget + 说明（≤1 行）      │
├────────────────────────┬─────────────────────────────────────────┤
│ 左列·输入（固定 470px）  │ 右列·输出（弹性伸展）                     │
│  输入卡                 │  输出预览卡（stretch=1）                  │
│   主提示词 TextEdit     │   预览区 min-height 430px 居中            │
│     min-height ≥150px  │   占位文案 → 生成后大图（保持比例填宽）     │
│   负面提示词 ≥64px      │   点击图片 = 系统看图器打开原图            │
│   参考图（按路径显隐）   │  操作条：[打开文件夹] [打开原图]  共N张     │
│  参数卡（行卡规格 §3.1） │                                          │
│   每行 = label 110px    │                                          │
│   + 控件弹性填满        │                                          │
│   相关参数同 row 合并   │                                          │
│  显存卡                 │                                          │
│   ✓完成后释放显存(勾选) │                                          │
│   [立即释放显存] 空闲MB │                                          │
│  高级卡（默认折叠）      │                                          │
├────────────────────────┴─────────────────────────────────────────┤
│ 运行条（全宽）：[生成] [取消]  进度条(弹性)  状态文字               │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 参数行卡规格（解决「有的太宽、输入框太窄」）

- 每个参数一行：**label 固定 110px（BodyLabel）+ 控件弹性 stretch=1 填满行宽**
- 同类小参数合并一行（如 `比例/规格`、`数量/种子`、`步数/CFG`），每个控件仍等分弹性
- 主提示词输入区 **min-height ≥150px**，负面 ≥64px——输入区是用户停留最久的地方，给足面积
- 数字微调控件不设固定宽（等分弹性）；图标按钮固定 34-72px
- 禁止：控件固定死宽度、label 占半行、输入框压缩到 <160px 宽

### 3.2 输出预览规格

- 预览区最小高度 **430px**，图片按宽度适配（`scaledToWidth(min(容器宽-24, 900))`）居中
- 生成前显示占位文案；生成后自动切换大图；**点击图片 = `os.startfile` 系统看图器**
- 操作条必配：**[打开文件夹]**（`os.startfile(输出目录)`）+ [打开原图] + 张数统计
- 多张产物：先显示最后一张；「打开文件夹」承担多图浏览

### 3.3 显存区规格（通用，自动适配）

- **「完成后释放显存」CheckBox**（勾=跑完卸载+清缓存；不勾=模型常驻连出更快）——不叫「一次/连续」，用行为描述
- **[立即释放显存] 独立按钮**——随时可点，只卸载 GPU 权重不清缓存
- 显存空闲 MB 常驻显示（运行条/显存卡）

### 3.4 高级区局部滚动（必做）

- 高级折叠内容一律包进 `SmoothScrollArea`（透明背景无边框，`setMaximumHeight(420)`）——展开后**卡内局部滚动**，绝不撑爆左列/窗口
- 次级文本（负面提示词等）置高级区顶部；输入卡只留主提示词（§3.1 不变）

## 4. 单选 / 多选判定律

- 判定问题：「选项 A 和 B 能同时为真吗？」能 → 多选（CheckBox 组）；不能 → 单选（SegmentedWidget/ComboBox/RadioButton）
- 禁止用多选表达互斥概念；禁止用单选表达可叠加概念
- 可叠加项（LoRA 启用集合、资源清理类别）恒为多选；模型文件、预设、采样器恒为单选

## 5. 功能矛盾检查（S1 产出 conflict_matrix，UI 生成前必过）

对每对可能冲突的参数写 `{a, b, rule, resolution}`，典型必查：
- 「完成后释放显存」勾选 vs 图内卸载类节点 → 执行期统一覆写（manifest vram_sync_nodes），UI 文案「由显存勾选接管」
- 预设≠Custom vs 分层权重 → visible_when 锁定
- seed=-1（随机）vs 复现诉求 → 输出 sidecar 记录实际 seed，UI 显示「本次实际 seed」
- 数量>1 vs 释放显存时机 → 批次全部完成后统一卸载
- 用户未选中的参数（进高级区）被核心参数依赖时 → 高级值随核心默认联动，冲突时以核心为准
