# -*- coding: utf-8 -*-
"""双击启动器：读 config → 用所选 venv 的 pythonw 拉起 mossapp.ui。"""
import os
import subprocess
import sys
import json

if getattr(sys, 'frozen', False):
    PROG = os.path.dirname(os.path.abspath(sys.argv[0]))   # PyInstaller onefile: 用 exe 所在目录
else:
    PROG = os.path.dirname(os.path.abspath(__file__))


def main():
    cfg_path = os.path.join(PROG, 'config.json')
    venv = '.venv'
    try:
        with open(cfg_path, encoding='utf-8') as f:
            venv = json.load(f).get('python_env') or '.venv'
    except OSError:
        pass
    pyw = os.path.join(venv, 'Scripts', 'pythonw.exe')
    py = pyw if os.path.isfile(pyw) else os.path.join(venv, 'Scripts', 'python.exe')
    if not os.path.isfile(py):
        py = sys.executable
    # CREATE_NO_WINDOW: 不闪黑窗
    subprocess.Popen([py, '-m', 'mossapp', 'ui'], cwd=PROG,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
