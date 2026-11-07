# -*- coding: utf-8 -*-
"""通用 schema 驱动 UI（qfluentwidgets，双列布局）：任何工作流的 params_schema.json 直接渲染。

布局（skill references/ui-style.md §3）：左列 470px 输入/参数/显存/高级折叠 + 右列大预览。
架构（坑31）：UI 进程零 CUDA；推理在 `mossapp serve` 常驻子进程。
用法：S3 默认安装为 mossapp/ui.py；per-flow 精修版可整体替换（参照 reference_ui_moss.py）。
"""
import os
import sys
import json
import logging
import threading
import subprocess

from . import bootstrap

# ---- pywin32 --target 安装路径修复（坑32） ----
_PYLIBS = bootstrap.PYLIBS
if os.path.isdir(_PYLIBS) and _PYLIBS not in sys.path:
    sys.path.insert(0, _PYLIBS)
for _sub in ('win32', os.path.join('win32', 'lib'), 'win32com', 'Pythonwin'):
    _d = os.path.join(_PYLIBS, _sub)
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)
_ss = os.path.join(_PYLIBS, 'pywin32_system32')
if os.path.isdir(_ss):
    os.add_dll_directory(_ss)

log = logging.getLogger('mossapp.ui')
LOG_FILE = os.path.join(bootstrap.PROG, 'mossapp.log')

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QStackedLayout
from qfluentwidgets import (InfoBar, InfoBarPosition, CardWidget, SimpleCardWidget,
                            PrimaryPushButton, PushButton, ToolButton, ComboBox,
                            EditableComboBox, LineEdit, TextEdit, SpinBox, DoubleSpinBox,
                            SwitchButton, CheckBox, ProgressBar, FluentIcon, SubtitleLabel,
                            CaptionLabel, BodyLabel, ImageLabel, SmoothScrollArea,
                            setTheme, Theme, setThemeColor)


# ============================ serve 桥接（常驻读取线程） ============================
class ServeBridge(QObject):
    progressChanged = Signal(int, int, str)
    resultReady = Signal(dict)
    runningChanged = Signal(bool)
    serviceLog = Signal(str)

    def __init__(self):
        super().__init__()
        self._proc = None
        self._reader = None
        self._lock = threading.Lock()
        self._running = False
        self.init_payload = None

    def _ensure_proc(self):
        p = self._proc
        if p is not None and p.poll() is None:
            return p
        p = subprocess.Popen(
            [sys.executable, '-m', 'mossapp', 'serve'],
            cwd=bootstrap.PROG, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'},   # 坑34
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._proc = p
        ready, boot_tail = '', []
        while True:
            line = p.stdout.readline()
            if not line:
                break
            if line.startswith('INIT '):
                try:
                    self.init_payload = json.loads(line[5:])
                except json.JSONDecodeError:
                    pass
                continue
            if 'READY' in line:
                ready = line
                break
            s = line.strip()
            if s:
                boot_tail.append(s)
                if len(boot_tail) > 4:
                    boot_tail.pop(0)
        if 'READY' not in ready:
            log.error('serve 握手失败: %s', boot_tail[-1] if boot_tail else 'EOF')
            self._proc = None
            raise RuntimeError('推理服务启动失败: ' + (boot_tail[-1] if boot_tail else 'EOF'))
        if self._reader is None or not self._reader.is_alive():
            self._reader = threading.Thread(target=self._reader_loop, args=(p,), daemon=True)
            self._reader.start()
        return p

    def _reader_loop(self, p):
        try:
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('PROGRESS '):
                    try:
                        _, d, t, name = line.split(' ', 3)
                        self.progressChanged.emit(int(d), int(t), name)
                    except ValueError:
                        pass
                elif line.startswith('RESULT '):
                    try:
                        self.resultReady.emit(json.loads(line[7:]))
                    except json.JSONDecodeError:
                        self.resultReady.emit({'ok': False, 'error': 'RESULT 解析失败'})
                else:
                    self.serviceLog.emit(line)
            self.serviceLog.emit('[serve] 推理服务已退出')
        except Exception as e:  # noqa
            log.warning('reader: %s', e)
            self.serviceLog.emit(f'[serve] 读取中断: {e}')

    def start_run(self, params: dict):
        if self._running:
            return
        threading.Thread(target=self._run_worker, args=(dict(params),), daemon=True).start()

    def _run_worker(self, params):
        try:
            with self._lock:
                self._running = True
            self.runningChanged.emit(True)
            p = self._ensure_proc()
            p.stdin.write(json.dumps(params, ensure_ascii=False) + '\n')
            p.stdin.flush()
        except Exception as e:  # noqa
            log.exception('提交任务失败')
            self.resultReady.emit({'ok': False, 'error': f'提交任务失败: {e}'})
            self._proc = None
            with self._lock:
                self._running = False
            self.runningChanged.emit(False)

    def release_vram(self):
        threading.Thread(target=self._release_worker, daemon=True).start()

    def _release_worker(self):
        try:
            p = self._ensure_proc()
            p.stdin.write('CMD release_vram\n')
            p.stdin.flush()
        except Exception as e:  # noqa
            self.resultReady.emit({'ok': False, 'action': 'release_vram', 'error': f'发送失败: {e}'})

    def cancel(self):
        p = self._proc
        if p is not None and p.poll() is None:
            p.terminate()
        self._proc = None
        self._reader = None
        with self._lock:
            self._running = False
        self.runningChanged.emit(False)


# ============================ 行卡小件 ============================
def _vcombo(combo, choices, value):
    combo.clear()
    combo.addItems([str(c) for c in choices])
    try:
        combo.setCurrentIndex([str(c) for c in choices].index(str(value)))
    except ValueError:
        combo.setCurrentIndex(0)


def _prow(label, *widgets):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    lab = BodyLabel(label, w)
    lab.setFixedWidth(110)
    h.addWidget(lab, 0, Qt.AlignTop)
    for x in widgets:
        h.addWidget(x, 1)
    return w


def _card(title, sub=None):
    card = SimpleCardWidget()
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(10)
    v.addWidget(SubtitleLabel(title, card))
    if sub:
        v.addWidget(CaptionLabel(sub, card))
    return card, v


# ============================ 生成页（schema 驱动，无硬编码字段） ============================
class GenerateInterface(QWidget):
    settingsRequested = Signal()   # 运行条齿轮 → 主窗设置对话框

    def __init__(self, bridge: ServeBridge, parent=None):
        super().__init__(parent)
        self.setObjectName('generateInterface')
        self.bridge = bridge
        from .runner import load_schema, default_params
        from .scan import scan
        self.schema = load_schema()
        self.defaults = default_params()
        self.cfg = bootstrap.load_config()
        try:
            self.scan_rep = scan()
        except Exception:  # noqa
            self.scan_rep = {'ok': False, 'missing': [], 'items': []}
        self.last_files = []
        self.controls = {}          # param_id -> (widget, kind)
        self._vis_items = {}        # 带条件显隐的参数
        self._build_ui()
        self.bridge.progressChanged.connect(self._on_progress)
        self.bridge.resultReady.connect(self._on_result)
        self.bridge.runningChanged.connect(self._on_running)

    # ---------- 通用控件工厂 ----------
    def _make(self, spec, parent):
        ctrl = spec.get('control', 'text')
        default = self.defaults.get(spec['id'], spec.get('default'))
        if ctrl == 'textarea':
            w = TextEdit(parent)
            w.setPlainText(str(default or ''))
            w.setMinimumHeight(64)
            kind = 'multiline'
        elif spec.get('presets'):
            # 特殊数值参数：深入插件源码后给出的预设档位 + 可自由键入（ui-style §2）
            # 坑39：qfluentwidgets 可编辑下拉是 EditableComboBox，ComboBox 无 setEditable
            w = EditableComboBox(parent)
            w.addItems([str(p) for p in spec['presets']])
            cur = default if default is not None else spec['presets'][0]
            w.setCurrentText(str(cur))
            kind = 'preset_num'
        elif ctrl == 'combo':
            w = ComboBox(parent)
            _vcombo(w, spec.get('choices') or [default], default)
            kind = 'combo'
        elif ctrl == 'int':
            w = SpinBox(parent)
            w.setRange(int(spec.get('min') or 0), int(spec.get('max') or 999999))
            w.setValue(int(default or 0))
            kind = 'int'
        elif ctrl in ('slider', 'float'):
            w = DoubleSpinBox(parent)
            w.setRange(float(spec.get('min') or 0.0), float(spec.get('max') or 1.0))
            w.setSingleStep(float(spec.get('step') or 0.01))
            w.setDecimals(2)
            w.setValue(float(default or 0.0))
            kind = 'float'
        elif ctrl == 'toggle':
            w = SwitchButton(parent)
            w.setChecked(bool(default))
            kind = 'bool'
        elif ctrl == 'seed':
            w = LineEdit(parent)
            w.setText(str(default if default is not None else -1))
            kind = 'seed'
        else:
            w = LineEdit(parent)
            w.setText(str(default or ''))
            kind = 'text'
        self.controls[spec['id']] = (w, kind)
        return w

    def _flat_specs(self, item):
        """group/单参数 → 扁平 binding 列表。"""
        out = []
        if item.get('bindings'):
            for b in item['bindings']:
                out.append({'id': b['id'], 'label': b.get('label') or b['id'],
                            'control': b.get('control', 'text'), 'choices': b.get('choices'),
                            'min': b.get('min'), 'max': b.get('max'), 'step': b.get('step'),
                            'default': b.get('default', self.defaults.get(b['id'])),
                            'field': b.get('field')})
        elif not item.get('field_prefix'):
            out.append({'id': item['id'], 'label': item.get('label') or item['id'],
                        'control': item.get('control', 'text'), 'choices': item.get('choices'),
                        'min': item.get('min'), 'max': item.get('max'), 'step': item.get('step'),
                        'default': item.get('default', self.defaults.get(item['id']))})
        return out

    def _bind_item(self, item, parent, layout):
        for spec in self._flat_specs(item):
            if spec.get('field') == '__state__':
                spec = dict(spec, control='toggle')
            layout.addWidget(_prow(spec['label'], self._make(spec, parent)))

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 8)
        root.setSpacing(8)
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # 左列（470px）
        left = QWidget()
        left.setFixedWidth(470)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        # 输入卡：仅首个（主）textarea 占大面积；次级文本（负面提示词等）不渲染（用户原则）
        text_items = [i for i in self.schema['core'] if i.get('control') == 'textarea']
        if text_items:
            icard, iv = _card('输入', text_items[0].get('effect'))
            big = self._make(text_items[0], icard)
            big.setMinimumHeight(170)   # ui-style §3.1 主输入面积
            iv.addWidget(big)
            lv.addWidget(icard)

        # 参数卡：core 其余非 app_param 项
        other = [i for i in self.schema['core']
                 if i.get('control') != 'textarea' and not i.get('app_param')]
        if other:
            pcard, pv = _card('参数')
            for item in other:
                self._bind_item(item, pcard, pv)
                if item.get('visible_when') and '=' in item['visible_when']:
                    self._vis_items[item['id']] = item['visible_when']
            lv.addWidget(pcard)

        # 显存卡（通用，自动）
        vcard, vv = _card('显存')
        self.chk_release = CheckBox('完成后释放显存（不勾选=模型常驻，连出更快）', vcard)
        self.chk_release.setChecked(True)
        vv.addWidget(self.chk_release)
        self.btn_free = PushButton('立即释放显存', vcard)
        self.btn_free.clicked.connect(self._release_vram)
        self.vram_cap = CaptionLabel('空闲 ? MB', vcard)
        vv.addWidget(_prow('', self.btn_free, self.vram_cap))
        lv.addWidget(vcard)
        # 高级参数默认全部不渲染（用户原则：弹窗没选的就用工作流默认值；需要时用 --ui 精修版或重开弹窗）
        lv.addStretch(1)

        # 右列：输出大预览（430px+，点击开原图）
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        ocard, ov = _card('输出预览', '点击图片用系统看图器打开原图')
        self.preview_stack = QWidget(ocard)
        ps = QStackedLayout(self.preview_stack)
        self.img_placeholder = BodyLabel('生成后此处显示大图', ocard)
        self.img_placeholder.setAlignment(Qt.AlignCenter)
        self.img_big = ImageLabel()
        self.img_big.hide()
        self.img_big.setCursor(Qt.PointingHandCursor)
        self.img_big.mousePressEvent = lambda _e: self._open_last_image()
        ps.addWidget(self.img_placeholder)
        ps.addWidget(self.img_big)
        self.preview_stack.setMinimumHeight(430)
        ov.addWidget(self.preview_stack, 1)
        ops = QHBoxLayout()
        ops.setContentsMargins(0, 0, 0, 0)
        btn_dir = PushButton('打开文件夹', ocard)
        btn_dir.setIcon(FluentIcon.FOLDER)
        btn_dir.clicked.connect(self._open_output_dir)
        btn_img = PushButton('打开原图', ocard)
        btn_img.clicked.connect(self._open_last_image)
        self.out_info = CaptionLabel('共 0 张', ocard)
        ops.addWidget(btn_dir)
        ops.addWidget(btn_img)
        ops.addStretch(1)
        ops.addWidget(self.out_info)
        ov.addLayout(ops)
        rv.addWidget(ocard, 1)
        body.addWidget(left, 0)
        body.addWidget(right, 1)

        # 运行条
        rcard = CardWidget(self)
        rh = QHBoxLayout(rcard)
        rh.setContentsMargins(16, 10, 16, 10)
        self.btn_run = PrimaryPushButton('生成', rcard)
        self.btn_run.setFixedWidth(110)
        self.btn_cancel = PushButton('取消', rcard)
        self.btn_cancel.setEnabled(False)
        self.progress = ProgressBar(rcard)
        self.status = BodyLabel('就绪', rcard)
        btn_settings = ToolButton(FluentIcon.SETTING, rcard)
        btn_settings.setToolTip('设置')
        rh.addWidget(self.btn_run)
        rh.addWidget(self.btn_cancel)
        rh.addWidget(self.progress, 1)
        rh.addWidget(self.status, 0)
        rh.addWidget(btn_settings)
        root.addWidget(rcard)
        self.btn_run.clicked.connect(self._do_run)
        self.btn_cancel.clicked.connect(self.bridge.cancel)
        btn_settings.clicked.connect(self.settingsRequested.emit)
        self._refresh_visible()

    # ---------- visible_when 联动 ----------
    def _refresh_visible(self):
        for pid, cond in list(self._vis_items.items()):
            src, _, want = cond.partition('=')
            src_w = self.controls.get(src)
            target = self.controls.get(pid, (None, None))[0]
            if src_w is None or target is None:
                continue
            src_widget, src_kind = src_w
            cur = src_widget.currentText() if src_kind == 'combo' else str(
                src_widget.value() if hasattr(src_widget, 'value') else '')
            target.parentWidget().setVisible(str(cur) == want)

    # ---------- 输出操作 ----------
    def _open_output_dir(self):
        out_dir = self.cfg.get('output_dir') or ''
        os.makedirs(out_dir, exist_ok=True)
        os.startfile(out_dir)   # noqa

    def _open_last_image(self):
        if self.last_files:
            os.startfile(self.last_files[-1])

    def _release_vram(self):
        self.vram_cap.setText('释放中…')
        self.bridge.release_vram()

    # ---------- 运行 ----------
    def _do_run(self):
        params = {'vram_mode': 'once' if self.chk_release.isChecked() else 'continuous'}
        for pid, (w, kind) in self.controls.items():
            if kind == 'multiline':
                params[pid] = w.toPlainText()
            elif kind == 'combo':
                params[pid] = w.currentText()
            elif kind == 'preset_num':
                try:
                    params[pid] = float(w.currentText())
                except ValueError:
                    params[pid] = None   # 非法输入置空 → runner 回落 schema 默认
            elif kind in ('int', 'float'):
                params[pid] = w.value()
            elif kind == 'bool':
                params[pid] = w.isChecked()
            elif kind == 'seed':
                try:
                    params[pid] = int(w.text())
                except ValueError:
                    params[pid] = -1
            else:
                params[pid] = w.text()
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText('提交任务…')
        self.bridge.start_run(params)

    def _on_progress(self, done, total, name):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f'{done}/{total} {name}')

    def _on_result(self, r):
        if r.get('action') == 'release_vram':
            if r.get('ok'):
                self.vram_cap.setText(f"显存空闲 {(r.get('memory') or {}).get('vram_free_mb', '?')} MB")
                InfoBar.success(title='显存已释放',
                                content=f"空闲 {(r.get('memory') or {}).get('vram_free_mb', '?')} MB",
                                orient=Qt.Horizontal, isClosable=True,
                                position=InfoBarPosition.TOP_RIGHT, duration=4000, parent=self)
            else:
                InfoBar.error(title='释放失败', content=str(r.get('error'))[:160],
                              orient=Qt.Horizontal, isClosable=True,
                              position=InfoBarPosition.TOP_RIGHT, duration=8000, parent=self)
            return
        if not r.get('action'):
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)
        if r.get('ok'):
            self.progress.setValue(100)
            mem = r.get('memory') or {}
            self.status.setText(f"完成 {r.get('seconds')}s · 显存空闲 {mem.get('vram_free_mb', '?')}MB")
            self.vram_cap.setText(f"显存空闲 {mem.get('vram_free_mb', '?')} MB")
            self.last_files = r.get('files') or []
            self.out_info.setText(f'共 {len(self.last_files)} 张')
            if self.last_files:
                try:
                    self.img_big.setImage(self.last_files[-1])
                    w = max(360, self.preview_stack.width() - 24)
                    self.img_big.scaledToWidth(min(w, 900))
                    self.img_big.show()
                    self.preview_stack.setCurrentWidget(self.img_big)
                except Exception:  # noqa
                    pass
            InfoBar.success(title='生成完成',
                            content=f"{len(self.last_files)} 张 · {r.get('seconds')}s",
                            orient=Qt.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP_RIGHT, duration=5000, parent=self)
        else:
            self.status.setText('失败: ' + str(r.get('error')))
            InfoBar.error(title='生成失败', content=str(r.get('error'))[:200],
                          orient=Qt.Horizontal, isClosable=True,
                          position=InfoBarPosition.TOP_RIGHT, duration=12000, parent=self)

    def _on_running(self, running):
        self.btn_run.setEnabled(not running)
        self.btn_cancel.setEnabled(running)


# ============================ 设置页 ============================
# ============================ 设置对话框 + 单窗（无 tab 导航，用户原则） ============================
class MainWindow(QWidget):
    """单窗：生成页即全部内容；设置走运行条齿轮按钮 → 模态对话框（无 tab 导航）。"""

    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        setThemeColor('#60CDFF')
        from .runner import load_schema
        self.setWindowTitle((load_schema().get('app') or '本地出图') + ' · 本地出图')
        self.resize(1240, 880)
        self.bridge = ServeBridge()
        self.gen = GenerateInterface(self.bridge)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.gen)
        self.gen.settingsRequested.connect(self._open_settings)

    def _open_settings(self):
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle('设置')
        dlg.setFixedWidth(680)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        les = {}
        for label, key in (('Python 环境', 'python_env'), ('ComfyUI 目录（只读）', 'comfyui_dir'),
                           ('插件目录', 'plugin_dir'), ('输入图目录', 'input_dir'),
                           ('输出目录', 'output_dir')):
            le = LineEdit(dlg)
            le.setText(str(self.gen.cfg.get(key) or ''))
            les[key] = le
            v.addWidget(_prow(label, le))
        le_models = LineEdit(dlg)
        le_models.setText(';'.join(self.gen.cfg.get('model_dirs') or []))
        les['model_dirs'] = le_models
        v.addWidget(_prow('模型目录(;分隔)', le_models))
        async_offload = SwitchButton(dlg)
        async_offload.setChecked(bool(self.gen.cfg.get('disable_async_offload')))
        v.addWidget(_prow('禁用异步换装', async_offload))
        v.addWidget(CaptionLabel('显存挤兑原生崩溃防护，推荐开启（坑30）；保存后重启程序生效', dlg))

        def save():
            self.gen.cfg['python_env'] = les['python_env'].text().strip()
            self.gen.cfg['comfyui_dir'] = les['comfyui_dir'].text().strip()
            self.gen.cfg['plugin_dir'] = les['plugin_dir'].text().strip()
            self.gen.cfg['input_dir'] = les['input_dir'].text().strip()
            self.gen.cfg['output_dir'] = les['output_dir'].text().strip()
            self.gen.cfg['model_dirs'] = [s.strip() for s in les['model_dirs'].text().split(';') if s.strip()]
            self.gen.cfg['disable_async_offload'] = async_offload.isChecked()
            bootstrap.save_config(self.gen.cfg)
            InfoBar.success(title='已保存', content='重启程序后生效',
                            orient=Qt.Horizontal, isClosable=True, position=InfoBarPosition.TOP,
                            duration=4000, parent=self)
            dlg.accept()

        h = QHBoxLayout()
        h.addStretch(1)
        btn_cancel = PushButton('取消', dlg)
        btn_cancel.clicked.connect(dlg.reject)
        btn_save = PrimaryPushButton('保存', dlg)
        btn_save.clicked.connect(save)
        h.addWidget(btn_cancel)
        h.addWidget(btn_save)
        v.addLayout(h)
        dlg.exec()


def run_ui() -> int:
    _fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.getLogger().addHandler(_fh)   # 坑33
    log.info('UI(generic schema 驱动·单窗) 启动')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(run_ui())
