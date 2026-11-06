# -*- coding: utf-8 -*-
"""QFluentWidgets UI v2（双列布局）：左输入 / 右大预览 + 运行条。

布局规范（skill references/ui-style.md §3）：
- 左列固定 470px：模式 → 输入（提示词大输入区）→ 参数（行卡：label 110px + 控件弹性）→ 显存 → 高级折叠
- 右列弹性：输出大预览（点击用系统看图器打开）+ 操作条（打开文件夹 / 打开图片）
- 运行条：✓完成后释放显存（勾选=跑完卸载）+ [释放显存] 独立按钮 + 进度 + [生成][取消]
- 架构（坑31）：UI 进程零 CUDA；推理在 `mossapp serve` 常驻子进程，常驻读取线程分发 INIT/READY/PROGRESS/RESULT。
"""
import os
import sys
import json
import logging
import threading
import subprocess

from . import bootstrap

# ---- pywin32 --target 安装路径修复（.pth 在非 site 目录不执行，坑32） ----
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
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedLayout,
                               QFileDialog)
from qfluentwidgets import (FluentWindow, NavigationItemPosition, SegmentedWidget, InfoBar,
                            InfoBarPosition, CardWidget, SimpleCardWidget, PrimaryPushButton,
                            PushButton, ToolButton, ComboBox, LineEdit, TextEdit, SpinBox,
                            DoubleSpinBox, SwitchButton, CheckBox, ProgressBar, FluentIcon,
                            SubtitleLabel, StrongBodyLabel, CaptionLabel, BodyLabel,
                            SmoothScrollArea, ImageLabel, setTheme, Theme, setThemeColor)


# ============================ serve 桥接（常驻读取线程） ============================
class ServeBridge(QObject):
    """`mossapp serve` 常驻推理子进程客户端；读取线程单例常驻分发所有回传行。"""
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
            # stdout 关闭 = 服务退出
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
        """通用功能：独立释放显存按钮 → CMD 行，结果走 resultReady（action=release_vram）。"""
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


def _prow(label, *widgets, sub=None):
    """参数行卡规格：label 固定 110px + 控件弹性填充（解决宽窄失衡）。"""
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


# ============================ 生成页 ============================
class GenerateInterface(QWidget):
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
        self._mode_item = next(i for i in self.schema['core'] if i['id'] == 'mode')
        self._spec_item = next(i for i in self.schema['core'] if i['id'] == 'spec')
        self.last_files = []
        self._build_ui()
        self.bridge.progressChanged.connect(self._on_progress)
        self.bridge.resultReady.connect(self._on_result)
        self.bridge.runningChanged.connect(self._on_running)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 8)
        root.setSpacing(8)

        # 顶部：模式卡（紧凑一行，单 exe 三模式）
        mcard, mv = _card('出图模式', None)
        self.mode_seg = SegmentedWidget(mcard)
        for o in self._mode_item['options']:
            self.mode_seg.addItem(routeKey=str(o['value']), text=o['label'],
                                  onClick=lambda k=str(o['value']): self._set_mode(int(k)))
        self.mode_seg.setCurrentItem(str(self._mode_item['default']))
        self.mode_seg.setFixedHeight(34)
        self.mode_note = CaptionLabel('', mcard)
        mh = QHBoxLayout()
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.mode_seg, 0)
        mh.addWidget(self.mode_note, 1)
        mv.addLayout(mh)
        root.addWidget(mcard)

        # 双列主体
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # ---- 左列：输入（固定宽） ----
        left = QWidget()
        left.setFixedWidth(470)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        icard, iv = _card('输入')
        self.te_user = TextEdit(icard)
        self.te_user.setPlainText(str(self.defaults.get('text_user') or ''))
        self.te_user.setMinimumHeight(150)
        self.te_user.setObjectName('text_user')
        iv.addWidget(_prow('需求提示词', self.te_user))
        self.te_neg = TextEdit(icard)
        self.te_neg.setPlainText(str(self.defaults.get('text_negative') or ''))
        self.te_neg.setMinimumHeight(64)
        self.te_neg.setObjectName('text_negative')
        iv.addWidget(_prow('负面提示词', self.te_neg))
        self.ref_image = LineEdit(icard)
        self.ref_image.setPlaceholderText('模式③专用，单文件')
        ref_btn = PushButton('选择…', icard)
        ref_btn.setFixedWidth(72)
        ref_btn.clicked.connect(self._pick_ref)
        self.ref_row = _prow('参考图', self.ref_image, ref_btn)
        iv.addWidget(self.ref_row)
        lv.addWidget(icard)

        pcard, pv = _card('参数')
        self.ratio = ComboBox(pcard)
        _vcombo(self.ratio, self._core('ratio')['choices'], self.defaults.get('ratio'))
        self.spec = ComboBox(pcard)
        _vcombo(self.spec, self._core('spec')['choices'], self.defaults.get('spec'))
        self.spec.currentTextChanged.connect(lambda _: self._apply_spec())
        self.mp = DoubleSpinBox(pcard)
        self.mp.setRange(0.25, 4.0); self.mp.setSingleStep(0.05)
        self.mp.setValue(float(self.defaults.get('mp') or 2.0))
        self.mp.valueChanged.connect(lambda _: self._update_preview_size())
        pv.addWidget(_prow('比例 / 规格', self.ratio, self.spec))
        pv.addWidget(_prow('百万像素', self.mp))
        self.mp_preview = CaptionLabel('', pcard)
        pv.addWidget(self.mp_preview)

        self.merge_preset = ComboBox(pcard)
        mb = self._binding('merge', 'merge_preset')
        _vcombo(self.merge_preset, mb.get('choices', []), self.defaults.get('merge_preset'))
        self.merge_preset.currentTextChanged.connect(lambda _: self._apply_preset())
        self.merge_strength = DoubleSpinBox(pcard)
        self.merge_strength.setRange(0, 1); self.merge_strength.setSingleStep(0.01)
        self.merge_strength.setValue(float(self.defaults.get('merge_strength') or 0.3))
        pv.addWidget(_prow('预设 / 强度', self.merge_preset, self.merge_strength))

        self.model_combos = {}
        missing = set(self.scan_rep.get('missing') or [])
        for bid, label in (('unet_base', 'UNET基座'), ('unet_sft', 'UNET-SFT'),
                           ('clip', '文本编码器'), ('vae', 'VAE')):
            b = self._binding('models', bid)
            combo = ComboBox(pcard)
            _vcombo(combo, [b.get('default')], self.defaults.get(bid) or b.get('default'))
            self.model_combos[bid] = combo
            dot = '　●缺失' if b.get('default') in missing else ''
            pv.addWidget(_prow(label + dot, combo))

        self.batch = SpinBox(pcard)
        self.batch.setRange(1, 8); self.batch.setValue(int(self.defaults.get('batch') or 1))
        self.seed = LineEdit(pcard)
        self.seed.setText(str(self.defaults.get('seed', -1)))
        seed_btn = ToolButton(FluentIcon.SYNC, pcard)
        seed_btn.setFixedWidth(34)
        seed_btn.clicked.connect(lambda: self.seed.setText(str(__import__('random').getrandbits(53))))
        pv.addWidget(_prow('数量 / 种子', self.batch, self.seed, seed_btn))
        lv.addWidget(pcard)

        vcard, vv = _card('显存', None)
        self.chk_release = CheckBox('完成后释放显存（不勾选=模型常驻，连出更快）', vcard)
        self.chk_release.setChecked(True)
        vv.addWidget(self.chk_release)
        self.btn_free = PushButton('立即释放显存', vcard)
        self.btn_free.clicked.connect(self._release_vram)
        self.vram_cap = CaptionLabel('空闲 ? MB', vcard)
        vv.addWidget(_prow('', self.btn_free, self.vram_cap))
        lv.addWidget(vcard)

        acard, av = _card('高级')
        self.adv_toggle = PushButton('展开高级参数', acard)
        self.adv_toggle.clicked.connect(self._toggle_advanced)
        av.addWidget(self.adv_toggle)
        self.adv_body = QWidget(acard)
        adv_v = QVBoxLayout(self.adv_body)
        adv_v.setContentsMargins(0, 6, 0, 0)
        adv_v.setSpacing(8)
        adv_v.addWidget(self._adv_ksampler())
        adv_v.addWidget(self._adv_llm())
        adv_v.addWidget(self._adv_lora())
        adv_v.addWidget(self._adv_output())
        self.adv_body.hide()
        av.addWidget(self.adv_body)
        lv.addWidget(acard)
        lv.addStretch(1)

        # ---- 右列：输出大预览（弹性） ----
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
        self.btn_open_dir = PushButton('打开文件夹', ocard)
        self.btn_open_dir.setIcon(FluentIcon.FOLDER)
        self.btn_open_dir.clicked.connect(self._open_output_dir)
        self.btn_open_img = PushButton('打开原图', ocard)
        self.btn_open_img.clicked.connect(self._open_last_image)
        self.out_info = CaptionLabel('共 0 张', ocard)
        ops.addWidget(self.btn_open_dir)
        ops.addWidget(self.btn_open_img)
        ops.addStretch(1)
        ops.addWidget(self.out_info)
        ov.addLayout(ops)
        rv.addWidget(ocard, 1)
        body.addWidget(left, 0)
        body.addWidget(right, 1)

        # ---- 运行条 ----
        rcard = CardWidget(self)
        rh = QHBoxLayout(rcard)
        rh.setContentsMargins(16, 10, 16, 10)
        self.btn_run = PrimaryPushButton('生成', rcard)
        self.btn_run.setFixedWidth(110)
        self.btn_cancel = PushButton('取消', rcard)
        self.btn_cancel.setEnabled(False)
        self.progress = ProgressBar(rcard)
        self.status = BodyLabel('就绪', rcard)
        rh.addWidget(self.btn_run)
        rh.addWidget(self.btn_cancel)
        rh.addWidget(self.progress, 1)
        rh.addWidget(self.status, 0)
        root.addWidget(rcard)

        self._apply_mode()
        self._apply_spec()
        self._apply_preset()
        self._refresh_vram_caption()

    # ---------- 小件 ----------
    def _core(self, id_):
        return next(i for i in self.schema['core'] if i['id'] == id_)

    def _binding(self, gid, bid):
        return next(b for b in self._core(gid)['bindings'] if b['id'] == bid)

    def _pick_ref(self):
        f, _ = QFileDialog.getOpenFileName(self, '选择参考图',
                                           self.cfg.get('input_dir', ''), '图片 (*.png *.jpg *.jpeg *.webp)')
        if f:
            self.ref_image.setText(f)

    # ---------- 联动 ----------
    def _set_mode(self, v):
        self._mode = v
        self.mode_seg.setCurrentItem(str(v))
        self._apply_mode()

    def _apply_mode(self):
        m = (self._mode_item.get('switch_map') or {}).get(str(getattr(self, '_mode', self._mode_item['default'])), {})
        self.ref_row.setVisible('ref_image' in (m.get('visible') or []))
        self.mode_note.setText(m.get('note', ''))

    def _apply_spec(self):
        m = (self._spec_item.get('switch_map') or {}).get(self.spec.currentText(), {})
        self.mp.setVisible('mp' in (m.get('visible') or []))
        self.mp_preview.setVisible('mp' in (m.get('visible') or []))
        self._update_preview_size()

    def _apply_preset(self):
        if hasattr(self, 'adv_merge_custom'):
            self.adv_merge_custom.setVisible(self.merge_preset.currentText() == 'Custom')

    def _update_preview_size(self):
        import re
        m = re.match(r'^(\d+):(\d+)$', self.ratio.currentText())
        rw, rh = (int(m.group(1)), int(m.group(2))) if m else (9, 16)
        px = float(self.mp.value()) * 1e6
        scale = (px / (rw * rh)) ** 0.5
        w = round(rw * scale / 16) * 16
        h = round(rh * scale / 16) * 16
        self.mp_preview.setText(f'实际宽高 ≈ {w}×{h}')

    # ---------- 高级 ----------
    def _adv_ksampler(self):
        card, v = _card('采样与引导')
        self.steps = SpinBox(card); self.steps.setRange(1, 100); self.steps.setValue(int(self.defaults.get('steps') or 25))
        self.cfg_ = DoubleSpinBox(card); self.cfg_.setRange(1, 12); self.cfg_.setSingleStep(0.1); self.cfg_.setValue(float(self.defaults.get('cfg') or 3.5))
        self.sampler = ComboBox(card); _vcombo(self.sampler, ['er_sde'], self.defaults.get('sampler_name'))
        self.scheduler = ComboBox(card); _vcombo(self.scheduler, ['simple'], self.defaults.get('scheduler'))
        self.denoise = DoubleSpinBox(card); self.denoise.setRange(0, 1); self.denoise.setSingleStep(0.01); self.denoise.setValue(float(self.defaults.get('denoise') or 1))
        v.addWidget(_prow('步数 / CFG', self.steps, self.cfg_))
        v.addWidget(_prow('采样器 / 调度', self.sampler, self.scheduler))
        v.addWidget(_prow('去噪', self.denoise))
        self.skip_single = LineEdit(card); self.skip_single.setText(str(self.defaults.get('skip_single') or '22'))
        self.skip_start = DoubleSpinBox(card); self.skip_start.setRange(0, 1); self.skip_start.setSingleStep(0.01); self.skip_start.setValue(float(self.defaults.get('skip_start') or 0))
        self.skip_end = DoubleSpinBox(card); self.skip_end.setRange(0, 1); self.skip_end.setSingleStep(0.01); self.skip_end.setValue(float(self.defaults.get('skip_end') or 0.5))
        v.addWidget(_prow('跳过层(单层)', self.skip_single))
        v.addWidget(_prow('层跳过起止', self.skip_start, self.skip_end))
        return card

    def _adv_llm(self):
        card, v = _card('提示词 LLM（模式①不加载；「推理后卸载」由显存勾选统一接管）')
        self.llm_model = ComboBox(card); _vcombo(self.llm_model, [self.defaults.get('llm_model') or ''], self.defaults.get('llm_model'))
        self.llm_ctx = SpinBox(card); self.llm_ctx.setRange(512, 65536); self.llm_ctx.setValue(int(self.defaults.get('llm_ctx') or 8192))
        self.llm_gpu = SpinBox(card); self.llm_gpu.setRange(-1, 128); self.llm_gpu.setValue(int(self.defaults.get('llm_gpu_layers') or -1))
        self.h3_max_tokens = SpinBox(card); self.h3_max_tokens.setRange(64, 32768); self.h3_max_tokens.setValue(int(self.defaults.get('h3_max_tokens') or 2048))
        v.addWidget(_prow('主模型', self.llm_model))
        v.addWidget(_prow('上下文 / GPU层', self.llm_ctx, self.llm_gpu))
        v.addWidget(_prow('max_tokens', self.h3_max_tokens))
        return card

    def _adv_lora(self):
        card, v = _card('LoRA（可叠加，勾选=启用）')
        for item in self.schema['advanced']:
            if not item['id'].startswith('lora'):
                continue
            row_widgets = []
            for b in item['bindings']:
                if b['field'] == '__state__':
                    cb = CheckBox('启用', card)
                    cb.setChecked(bool(self.defaults.get(b['id'])))
                    setattr(self, b['id'], cb)
                    row_widgets.append(cb)
                elif b['field'] == 'lora_name':
                    combo = ComboBox(card)
                    _vcombo(combo, [b.get('default')], self.defaults.get(b['id']) or b.get('default'))
                    setattr(self, b['id'], combo)
                    row_widgets.append(combo)
                elif b['field'] == 'strength_model':
                    sp = DoubleSpinBox(card); sp.setRange(-4, 4); sp.setSingleStep(0.01)
                    sp.setValue(float(self.defaults.get(b['id']) or 1))
                    setattr(self, b['id'], sp)
                    row_widgets.append(sp)
            v.addWidget(_prow(item['label'], *row_widgets))
        return card

    def _adv_output(self):
        card, v = _card('输出')
        self.output_prefix = LineEdit(card)
        self.output_prefix.setText(str(self.defaults.get('output_prefix') or 'moss_image_t2i'))
        v.addWidget(_prow('文件名前缀', self.output_prefix))
        v.addWidget(CaptionLabel('落盘 = 前缀 + 时间戳子目录，绝不覆盖', card))
        v.addWidget(CaptionLabel('Custom 分层权重（50 项）v1 沿用工作流原值', card))
        return card

    def _toggle_advanced(self):
        show = self.adv_body.isHidden()
        self.adv_body.setVisible(show)
        self.adv_toggle.setText('收起高级参数' if show else '展开高级参数')

    # ---------- 输出操作 ----------
    def _open_output_dir(self):
        out_dir = self.cfg.get('output_dir') or ''
        os.makedirs(out_dir, exist_ok=True)
        os.startfile(out_dir)   # noqa: 资源管理器打开

    def _open_last_image(self):
        if self.last_files:
            os.startfile(self.last_files[-1])

    def _release_vram(self):
        self.vram_cap.setText('释放中…')
        self.bridge.release_vram()

    def _refresh_vram_caption(self, mb=None):
        self.vram_cap.setText(f'显存空闲 {mb if mb is not None else "?"} MB')

    # ---------- 运行 ----------
    def _do_run(self):
        params = {
            'mode': int(getattr(self, '_mode', self._mode_item['default'])),
            'text_user': self.te_user.toPlainText(),
            'text_negative': self.te_neg.toPlainText(),
            'ref_image': os.path.basename(self.ref_image.text().strip() or 'example.png'),
            'ratio': self.ratio.currentText(), 'spec': self.spec.currentText(),
            'mp': self.mp.value(), 'merge_preset': self.merge_preset.currentText(),
            'merge_strength': self.merge_strength.value(),
            'batch': self.batch.value(), 'seed': self._int(self.seed.text(), -1),
            'vram_mode': 'once' if self.chk_release.isChecked() else 'continuous',
            'steps': self.steps.value(), 'cfg': self.cfg_.value(),
            'sampler_name': self.sampler.currentText(), 'scheduler': self.scheduler.currentText(),
            'denoise': self.denoise.value(),
            'skip_single': self.skip_single.text(),
            'skip_start': self.skip_start.value(), 'skip_end': self.skip_end.value(),
            'llm_model': self.llm_model.currentText(), 'llm_ctx': self.llm_ctx.value(),
            'llm_gpu_layers': self.llm_gpu.value(), 'h3_max_tokens': self.h3_max_tokens.value(),
            'lora90_name': self.lora90_name.currentText(), 'lora90_strength': self.lora90_strength.value(),
            'lora41_enable': self.lora41_enable.isChecked(),
            'lora41_name': self.lora41_name.currentText(), 'lora41_strength': self.lora41_strength.value(),
            'output_prefix': self.output_prefix.text(),
        }
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText('提交任务…')
        self.bridge.start_run(params)

    @staticmethod
    def _int(s, default):
        try:
            return int(s)
        except (TypeError, ValueError):
            return default

    def _on_progress(self, done, total, name):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f'{done}/{total} {name}')

    def _on_result(self, r):
        if r.get('action') == 'release_vram':
            if r.get('ok'):
                self._refresh_vram_caption((r.get('memory') or {}).get('vram_free_mb'))
                InfoBar.success(title='显存已释放', content=f"空闲 {(r.get('memory') or {}).get('vram_free_mb', '?')} MB",
                                orient=Qt.Horizontal, isClosable=True,
                                position=InfoBarPosition.TOP_RIGHT, duration=4000, parent=self)
            else:
                self._refresh_vram_caption('?')
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
            self._refresh_vram_caption(mem.get('vram_free_mb'))
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
            InfoBar.success(title='生成完成', content=f"{len(self.last_files)} 张 · {r.get('seconds')}s",
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
class SettingsInterface(QWidget):
    def __init__(self, bridge: ServeBridge, gen: GenerateInterface, parent=None):
        super().__init__(parent)
        self.setObjectName('settingsInterface')
        self.gen = gen
        self.cfg = gen.cfg
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 12, 24, 12)
        v.setSpacing(10)
        v.addWidget(SubtitleLabel('设置'))
        self.les = {}
        for label, key in (('Python 环境', 'python_env'), ('ComfyUI 目录（只读）', 'comfyui_dir'),
                           ('插件目录', 'plugin_dir'), ('输入图目录', 'input_dir'),
                           ('输出目录', 'output_dir')):
            le = LineEdit(self)
            le.setText(str(self.cfg.get(key) or ''))
            self.les[key] = le
            v.addWidget(_prow(label, le))
        le_models = LineEdit(self)
        le_models.setText(';'.join(self.cfg.get('model_dirs') or []))
        self.les['model_dirs'] = le_models
        v.addWidget(_prow('模型目录(;分隔)', le_models))
        self.async_offload = SwitchButton(self)
        self.async_offload.setChecked(bool(self.cfg.get('disable_async_offload')))
        v.addWidget(_prow('禁用异步换装', self.async_offload))
        v.addWidget(CaptionLabel('显存挤兑原生崩溃防护，推荐开启（坑30）', self))
        save = PrimaryPushButton('保存设置', self)
        save.clicked.connect(self._save)
        v.addWidget(save)
        v.addStretch(1)

    def _save(self):
        self.cfg['python_env'] = self.les['python_env'].text().strip()
        self.cfg['comfyui_dir'] = self.les['comfyui_dir'].text().strip()
        self.cfg['plugin_dir'] = self.les['plugin_dir'].text().strip()
        self.cfg['input_dir'] = self.les['input_dir'].text().strip()
        self.cfg['output_dir'] = self.les['output_dir'].text().strip()
        self.cfg['model_dirs'] = [s.strip() for s in self.les['model_dirs'].text().split(';') if s.strip()]
        self.cfg['disable_async_offload'] = self.async_offload.isChecked()
        bootstrap.save_config(self.cfg)
        InfoBar.success(title='已保存', content='重启推理服务后生效（重新打开程序即可）',
                        orient=Qt.Horizontal, isClosable=True, position=InfoBarPosition.TOP,
                        duration=5000, parent=self)


# ============================ 主窗口 ============================
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        setThemeColor('#60CDFF')
        self.bridge = ServeBridge()
        self.gen = GenerateInterface(self.bridge)
        self.sett = SettingsInterface(self.bridge, self.gen)
        self.addSubInterface(self.gen, FluentIcon.PLAY, '生成')
        self.addSubInterface(self.sett, FluentIcon.SETTING, '设置', NavigationItemPosition.BOTTOM)
        self.setWindowTitle('MOSS 全能王 · 本地出图')
        self.resize(1240, 880)


def run_ui() -> int:
    _fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.getLogger().addHandler(_fh)   # 二次 basicConfig 是 no-op（坑33）
    log.info('UI(qfluentwidgets v2 双列) 启动')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(run_ui())
