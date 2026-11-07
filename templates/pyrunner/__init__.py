# -*- coding: utf-8 -*-
import os
import sys

# 程序根/pylibs 在任何入口（ui/cli/launcher）导入 runner 时即可用
PROG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYLIBS = os.path.join(PROG, 'pylibs')
if os.path.isdir(PYLIBS) and PYLIBS not in sys.path:
    sys.path.insert(0, PYLIBS)
