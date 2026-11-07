# -*- coding: utf-8 -*-
"""S6 UI 自检（用户要求固化的检测过程）：offscreen 实例化真实 UI，五检全过才算交付。

检 1 控件覆盖：schema core 每个参数都有控件，且无多余控件（三硬禁②：高级不渲染）
检 2 默认值显示：每个控件显示值 == schema/plan 期望值
检 3 键入+clamp：数字类控件模拟键入 → _num 解析并 clamp 到 schema 范围
检 4 visible_when 联动：源参数切换 → 目标控件显隐翻转
检 5 渲染截图：整窗 grab 存盘（人工可复核的视觉证据）

用法：python s6_ui_check.py --program <程序根> --venv <python.exe>
输出：JSON 报告（每检 ok/明细）+ 截图路径；任一检失败 exit 1。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--program', required=True)
ap.add_argument('--venv', required=True)
a = ap.parse_args()

PROG = os.path.abspath(a.program)
# 定位 UI 包目录（含 ui.py 的目录）
pkg = None
for d in sorted(os.listdir(PROG)):
    full = os.path.join(PROG, d)
    if os.path.isdir(full) and os.path.isfile(os.path.join(full, 'ui.py')):
        pkg = d
if not pkg:
    sys.exit('未找到 UI 包（含 ui.py 的目录）')

CHECK = r'''
# -*- coding: utf-8 -*-
import os, sys, json
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, r'__PROG__')
from PySide6.QtWidgets import QApplication
app = QApplication([])
import importlib
ui_mod = importlib.import_module('__PKG__.ui')
bridge = ui_mod.ServeBridge()
g = ui_mod.GenerateInterface(bridge)
schema = g.schema
checks = []
def add(name, ok, detail=''):
    checks.append({'check': name, 'ok': bool(ok), 'detail': str(detail)[:160]})

# 检 1 控件覆盖 + 无多余（三硬禁②）
core_ids = [i['id'] for i in schema['core'] if not i.get('app_param')]
missing = [i for i in core_ids if i not in g.controls]
extra = [i for i in g.controls if i not in core_ids]
add('1a core 控件覆盖', not missing, f'缺: {missing}')
add('1b 无多余控件（高级不渲染）', not extra, f'多: {extra}')

# 检 2 默认值显示
def read_widget(pid):
    w, kind = g.controls.get(pid, (None, None))
    if w is None:
        return None, kind
    if kind == 'multiline':
        return w.toPlainText(), kind
    if kind in ('combo',):
        return w.currentText(), kind
    if kind in ('preset_num', 'int', 'float', 'seed'):
        return w.text(), kind
    if kind == 'bool':
        return w.isChecked(), kind
    return w.text(), kind

for item in schema['core']:
    if item.get('app_param'):
        continue
    v, kind = read_widget(item['id'])
    exp = item.get('default')
    if exp is None:
        exp = g.defaults.get(item['id'])
    if exp is not None:
        ok = str(exp) == str(v)
        add(f'2 默认值 {item["id"]}', ok, f'显示 {v!r} vs 期望 {exp!r}')

# 检 3 键入 + clamp（数字类）
for item in schema['core']:
    sp = g.specs.get(item['id'])
    pid = item['id']
    w, kind = g.controls.get(pid, (None, None))
    if w is None:
        continue
    if kind == 'preset_num':
        old = w.text()
        w.setText('99999')                       # 远超 max → clamp 到 max
        got = g._num(w.text(), pid)
        mx = sp.get('max') if sp else None
        add(f'3 键入clamp {pid}', mx is None or abs(float(got) - float(mx)) < 1e-6,
            f'输入 99999 → {got} (max={mx})')
        w.setText(old)
    elif kind == 'int':
        old = w.text()
        mn = int(sp.get('min') or 0) if sp else 0
        mx = int(sp.get('max') or 999999) if sp else 999999
        mid = (mn + mx) // 2                          # 域内值：验证键入原样生效
        w.setText(str(mid))
        got = g._num(w.text(), pid)
        add(f'3 键入 {pid}', int(got) == mid, f'输入 {mid} → 解析={got} (域 {mn}~{mx})')
        lo_test = mn - 5
        w.setText(str(lo_test))
        got_lo = g._num(w.text(), pid)
        add(f'3 clamp下界 {pid}', int(got_lo) == mn, f'输入 {lo_test} → {got_lo} (应钳到 {mn})')
        w.setText(old)
    elif kind == 'seed':
        old = w.text()
        w.setText('123456')
        got = int(float(g._num(w.text(), pid) if hasattr(g, '_num') else int(w.text())))
        add(f'3 键入 {pid}', got == 123456, f'解析={got}')
        w.setText(old)

# 检 4 visible_when 联动
for item in schema['core']:
    cond = item.get('visible_when')
    if not cond or '=' not in cond:
        continue
    src, _, want = cond.partition('=')
    sw, skind = g.controls.get(src, (None, None))
    tw, _ = g.controls.get(item['id'], (None, None))
    if sw is None or tw is None:
        continue
    if skind != 'combo':
        continue
    src_choices = next((i.get('choices') for i in schema['core'] if i['id'] == src), None) or []
    other = next((c for c in src_choices if c != want), None)
    # 命中 → 可见（未显式 hide）
    sw.setCurrentText(want)
    vis_when_on = not tw.parentWidget().isHidden()
    hid_when_off = None
    if other is not None:
        sw.setCurrentText(other)
        hid_when_off = tw.parentWidget().isHidden()
        sw.setCurrentText(want)
    add(f'4 联动 {item["id"]}', vis_when_on and (hid_when_off in (True, None)),
        f'命中可见={vis_when_on} 未命中隐藏={hid_when_off}')

# 检 5 渲染截图
shot = os.path.join(r'__PROG__', 'ui_check_screenshot.png')
try:
    g.grab().save(shot)
    add('5 渲染截图', os.path.isfile(shot) and os.path.getsize(shot) > 5000, shot)
except Exception as e:
    add('5 渲染截图', False, str(e))

print('UI_CHECK ' + json.dumps({'ok': all(c['ok'] for c in checks),
                                'checks': checks,
                                'screenshot': shot}, ensure_ascii=False))
'''

code = CHECK.replace('__PROG__', PROG.replace('\\', '/')).replace('__PKG__', pkg)
tmp = os.path.join(tempfile.gettempdir(), 'ui_check_code.py')
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(code)

env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', QT_QPA_PLATFORM='offscreen')
r = subprocess.run([a.venv, tmp], cwd=PROG, capture_output=True, text=True,
                   encoding='utf-8', errors='replace', env=env, timeout=300)
out = r.stdout or ''
err = r.stderr or ''
line = next((l for l in out.splitlines() if l.startswith('UI_CHECK ')), None)
if line is None:
    print(json.dumps({'ok': False, 'error': '自检脚本无输出',
                      'stdout_tail': out[-800:], 'stderr_tail': err[-800:]},
                     ensure_ascii=False, indent=1))
    sys.exit(1)
rep = json.loads(line[len('UI_CHECK '):])
rep['exit_code'] = r.returncode
print(json.dumps(rep, ensure_ascii=False, indent=1))
sys.exit(0 if rep['ok'] else 1)
