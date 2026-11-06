# -*- coding: utf-8 -*-
"""环境自举：配置加载、路径接线、快照与插件加载、节点注册表。

边界铁律：快照只读复用；本机 ComfyUI 目录只读；不启服务不开端口。
"""
import json
import os
import sys
import importlib
import contextlib
import asyncio
import types
import logging

log = logging.getLogger('mossapp')

PROG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(PROG, 'engine_snapshot')
PYLIBS = os.path.join(PROG, 'pylibs')
PLUGIN_DIR = os.path.join(PROG, 'plugins')
TEMP_DIR = os.path.join(PROG, 'temp')

REQUIRED_PACKS = ['ComfyUI-MOSS-Merge-Presets', 'ComfyUI-llama_Dapao', 'Goohaitools-comfyui',
                  'comfyui-easy-use', 'comfyui-impact-pack']


def load_config() -> dict:
    with open(os.path.join(PROG, 'config.json'), encoding='utf-8') as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(os.path.join(PROG, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def load_manifest() -> dict:
    with open(os.path.join(PROG, 'deps_manifest.json'), encoding='utf-8') as f:
        return json.load(f)


class Registry:
    def __init__(self):
        self.classes = {}        # class_type -> (pack, cls)
        self.cfg = None
        self.manifest = None


def _install_prompt_server_mock():
    if 'server' in sys.modules:
        return
    from unittest.mock import MagicMock
    srv = types.ModuleType('server')
    mock = MagicMock()
    mock.instance = MagicMock()
    srv.PromptServer = mock
    sys.modules['server'] = srv


def _wire_folder_paths(cfg: dict) -> None:
    import folder_paths as fp
    fp.set_output_directory(cfg.get('output_dir') or os.path.join(PROG, 'output'))
    fp.set_temp_directory(cfg.get('temp_dir') or TEMP_DIR)
    if cfg.get('input_dir'):
        fp.set_input_directory(cfg['input_dir'])
    os.makedirs(TEMP_DIR, exist_ok=True)
    # 模型多根注册：settings model_dirs 的每个子目录，is_default 放最前
    roots = cfg.get('model_dirs') or []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for sub in os.listdir(root):
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                try:
                    fp.add_model_folder_path(sub, p, is_default=True)
                except Exception as e:  # noqa
                    log.warning('add_model_folder_path %s: %s', sub, e)
    # 插件可能直接拼 models_dir（如 Dapao llama）→ 指到真实模型根
    real_root = next((r for r in roots if os.path.isdir(r)), None)
    if real_root:
        try:
            fp.models_dir = real_root
        except AttributeError:
            pass


def _load_packs(reg: Registry, packs=None) -> None:
    packs = packs or REQUIRED_PACKS   # 优先 manifest 驱动（deps_manifest.custom_nodes）
    root = reg.cfg.get('comfyui_dir')
    search_dirs = [PLUGIN_DIR, os.path.join(root, 'custom_nodes')] if root else [PLUGIN_DIR]
    for d in search_dirs:
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    devnull = open(os.devnull, 'w', encoding='utf-8', errors='replace')
    for pack in packs:
        if pack in sys.modules:
            m = sys.modules[pack]
        else:
            try:
                with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                    m = importlib.import_module(pack)
            except Exception as e:  # noqa
                log.error('插件导入失败 %s: %s: %s', pack, type(e).__name__, e)
                continue
        for k, v in getattr(m, 'NODE_CLASS_MAPPINGS', {}).items():
            reg.classes.setdefault(k, (pack, v))


def bootstrap() -> Registry:
    """一次性自举：路径 → 快照核心 → 官方 extra 初始化 → 插件包。"""
    reg = Registry()
    reg.cfg = load_config()
    reg.manifest = load_manifest()

    if PYLIBS not in sys.path and os.path.isdir(PYLIBS):
        sys.path.insert(0, PYLIBS)
    if SNAP not in sys.path:
        sys.path.insert(0, SNAP)

    _install_prompt_server_mock()

    # 显存竞争自适应：空闲 VRAM 不足 6GB 时进 lowvram（官方降档路径，防原生段错误）
    try:
        import comfy.cli_args as cli_args
        import torch
        if reg.cfg.get('disable_async_offload'):
            cli_args.args.disable_async_offload = True   # 外部应用挤兑时 2-stream 异步换装会 c0000005（坑30）
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info()
            if free < 6 * 1024 ** 3:
                cli_args.args.lowvram = True
                log.info('空闲 VRAM %.1fGB → lowvram 模式', free / 1024 ** 3)
    except Exception as e:  # noqa
        log.warning('vram probe: %s', e)

    _wire_folder_paths(reg.cfg)

    import nodes as core  # noqa: 快照根 nodes.py（含 comfy 包）
    try:
        asyncio.run(core.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False))
    except Exception as e:  # noqa
        log.warning('init_extra_nodes: %s: %s', type(e).__name__, e)
    for k, v in core.NODE_CLASS_MAPPINGS.items():
        reg.classes.setdefault(k, ('core/extras', v))

    _load_packs(reg, [e['pack'] for e in reg.manifest.get('custom_nodes', [])] or None)
    log.info('自举完成：%d 个类可用', len(reg.classes))
    return reg
