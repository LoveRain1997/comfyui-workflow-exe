# -*- coding: utf-8 -*-
"""S4 入口：PyInstaller onefile --noconsole launcher（无黑框）+ start.bat + 桌面 lnk。

用法：
  python scripts/s4_launcher.py --program <程序根> --venv <python.exe路径> [--name <exe名>]
"""
import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--program', required=True)
ap.add_argument('--venv', required=True, help='venv 的 python.exe（含 PyInstaller）')
ap.add_argument('--name', default=None, help='exe 名，默认取程序根目录名')
a = ap.parse_args()

PROG = os.path.abspath(a.program)
name = a.name or os.path.basename(PROG)
launcher = os.path.join(PROG, 'launcher.py')
if not os.path.isfile(launcher):
    sys.exit(f'缺 launcher.py: {launcher}')

# 坑37：默认 console 会有黑框 → 必须 --noconsole（windowed）
r = subprocess.run([a.venv, '-m', 'PyInstaller', '--onefile', '--noconsole', '--noconfirm',
                    '--clean', '--distpath', PROG, '--workpath', os.path.join(os.environ.get('TEMP', PROG), 'pyi_s4'),
                    '--specpath', os.path.join(os.environ.get('TEMP', PROG), 'pyi_s4'),
                    '--name', name, launcher],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
exe = os.path.join(PROG, name + '.exe')
print('build rc =', r.returncode, '|', exe, os.path.getsize(exe) if os.path.isfile(exe) else 'MISSING')
if r.returncode != 0:
    print(r.stdout[-1500:] if r.stdout else '', r.stderr[-800:] if r.stderr else '')
    sys.exit(r.returncode)

# start.bat 备选（cmd 启动 exe，本身不常驻）
with open(os.path.join(PROG, 'start.bat'), 'w', encoding='gbk', errors='replace') as f:
    f.write('@echo off\r\nrem %s - 双击启动\r\ncd /d "%%~dp0"\r\nstart "" "%s.exe"\r\n'
            % (name, name))

# 桌面快捷方式
try:
    import win32com.client  # 若 venv 有 pywin32；无则走 powershell
    ws = win32com.client.Dispatch('WScript.Shell')
    lnk = ws.CreateShortcut(os.path.join(ws.SpecialFolders['Desktop'], name + '.lnk'))
    lnk.TargetPath = exe
    lnk.WorkingDirectory = PROG
    lnk.Save()
    print('lnk created (pywin32)')
except Exception:  # noqa
    ps = (f"$ws=New-Object -ComObject WScript.Shell;"
          f"$l=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\{name}.lnk');"
          f"$l.TargetPath='{exe}';$l.WorkingDirectory='{PROG}';$l.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True)
    print('lnk created (powershell)')
