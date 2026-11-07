# -*- coding: utf-8 -*-
"""S3 骨架：程序根 + 快照闭包 + pyrunner 模板（--pkg-name 命名）+ 依赖预装 + config.json。"""
import argparse, json, os, shutil, subprocess, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--name', required=True)
ap.add_argument('--workflow', required=True)
ap.add_argument('--comfyui', required=True, help='ComfyUI 根目录')
ap.add_argument('--venv', required=True, help='venv 的 python.exe 或根目录')
ap.add_argument('--models', action='append', default=[], help='模型根，可多次')
ap.add_argument('--input', default=None, help='输入图目录')
ap.add_argument('--out-root', required=True, help='程序输出根目录')
ap.add_argument('--ui', default=None, help='按 ui_plan 生成的 per-flow ui.py（缺省=通用 ui_generic）')
ap.add_argument('--pkg-name', default='runner', help='生成的 Python 包名（默认 runner，勿与 ComfyUI 快照内包重名）')
ap.add_argument('--skill-dir', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
a = ap.parse_args()

PROG = os.path.join(a.out_root, a.name)
if os.path.isdir(PROG):
    sys.exit(f'已存在: {PROG}（换名或先删除）')
os.makedirs(PROG)
SNAP = os.path.join(PROG, 'engine_snapshot')
os.makedirs(SNAP)
for d in ('plugins', 'pylibs', 'output', 'temp'):
    os.makedirs(os.path.join(PROG, d))

# ---- 快照闭包（实测顺序，缺一即 import 崩，见母版坑清单）----
SRC = a.comfyui
for pkg in ('comfy', 'comfy_extras', 'comfy_api', 'comfy_execution', 'comfy_config', 'app', 'utils'):
    src = os.path.join(SRC, pkg)
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(SNAP, pkg), ignore=shutil.ignore_patterns('__pycache__'))
for f in ('folder_paths.py', 'nodes.py', 'latent_preview.py', 'node_helpers.py',
          'protocol.py', 'execution.py', 'comfyui_version.py'):
    src = os.path.join(SRC, f)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(SNAP, f))

# ---- pyrunner 模板（包名由 --pkg-name 指定，默认 runner） ----
tpl = os.path.join(a.skill_dir, 'templates', 'pyrunner')
dst = os.path.join(PROG, a.pkg_name)
os.makedirs(dst)
for f in os.listdir(tpl):
    if f.endswith('.py'):
        shutil.copy2(os.path.join(tpl, f), os.path.join(dst, f))


# ---- UI：默认装通用 schema 驱动 ui_generic.py（开箱即用）；--ui 提供 per-flow 精修版覆盖 ----
ui_src = a.ui if (a.ui and os.path.isfile(a.ui)) else os.path.join(a.skill_dir, 'templates', 'ui_generic.py')
shutil.copy2(ui_src, os.path.join(dst, 'ui.py'))

# ---- launcher + bat ----
shutil.copy2(os.path.join(a.skill_dir, 'templates', 'launcher.py'), os.path.join(PROG, 'launcher.py'))
with open(os.path.join(PROG, 'start.bat'), 'w', encoding='gbk', errors='replace') as f:
    f.write('@echo off\r\nrem %s - 双击启动\r\ncd /d "%%~dp0"\r\n"%s" -m runner ui\r\nif errorlevel 1 pause\r\n'
            % (a.name, os.path.join(a.venv, 'Scripts', 'pythonw.exe')))

# ---- config.json ----
cfg = {
    'python_env': a.venv.replace('\\', '/'),
    'comfyui_dir': SRC.replace('\\', '/'),
    'plugin_dir': os.path.join(PROG, 'plugins').replace('\\', '/'),
    'model_dirs': [m.replace('\\', '/') for m in a.models] or [os.path.join(SRC, 'models').replace('\\', '/')],
    'input_dir': (a.input or os.path.join(SRC, 'input')).replace('\\', '/'),
    'output_dir': os.path.join(PROG, 'output').replace('\\', '/'),
    'vram_mode': 'continuous',
    'disable_async_offload': True,
    'last_params': {},
}
json.dump(cfg, open(os.path.join(PROG, 'config.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ---- PySide6 + qfluentwidgets + pywin32 预装（需网络；pip 连带装进 pylibs 的 numpy 必删，防污染 venv 依赖） ----
r = subprocess.run([os.path.join(a.venv, 'Scripts', 'python.exe'), '-m', 'pip', 'install',
                    '--target', os.path.join(PROG, 'pylibs'), 'PySide6', 'PySide6-Fluent-Widgets', 'pywin32',
                    '--quiet', '--no-warn-script-location'], capture_output=True, text=True)
print('PySide6+FluentWidgets install rc =', r.returncode)
for stray in ('numpy', 'pip', 'setuptools', 'pkg_resources'):
    stray_dir = os.path.join(PROG, 'pylibs', stray)
    if os.path.isdir(stray_dir):
        shutil.rmtree(stray_dir, ignore_errors=True)
        print('removed stray from pylibs:', stray)

print(json.dumps({'program': PROG, 'engine_snapshot': SNAP,
                  'next': ['S0 recon', 'S1 gen_plan + AI 契约(params_schema/deps_manifest) 放程序根',
                           'S4 launcher exe', 'S5 accept']}, ensure_ascii=False, indent=1))
