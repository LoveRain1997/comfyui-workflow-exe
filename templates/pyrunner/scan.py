# -*- coding: utf-8 -*-
"""环境扫描：三色报告（✅复用 / ⬇可补齐 / ❌需手选）。"""
import os
import json
import logging

from . import bootstrap

log = logging.getLogger('mossapp.scan')


def scan(deep: bool = False) -> dict:
    cfg = bootstrap.load_config()
    manifest = bootstrap.load_manifest()
    items = []

    # Python 环境 + torch（只报告，不安装——方案①边界）
    venv = cfg.get('python_env') or ''
    py = os.path.join(venv, 'Scripts', 'python.exe')
    torch_ok, torch_ver, gpu = False, None, None
    if os.path.isfile(py):
        import subprocess
        code = ("import torch;"
                "print(torch.__version__, torch.cuda.is_available(),"
                "torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')")
        try:
            out = subprocess.run([py, '-I', '-c', code], capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=120).stdout.strip().split()
            torch_ver, torch_ok, gpu = out[0], True, out[2] if len(out) > 2 else '-'
        except Exception as e:  # noqa
            log.warning('torch probe: %s', e)
    items.append({'kind': 'runtime', 'name': 'python+torch', 'status': 'ok' if torch_ok else 'missing',
                  'detail': f'{torch_ver} {gpu or "CUDA不可用"}'})

    # 插件：有则复用（本机 ComfyUI custom_nodes），无则可补齐
    for entry in manifest['custom_nodes']:
        found = _find_pack(cfg, entry)
        if found:
            items.append({'kind': 'plugin', 'name': entry['pack'], 'status': 'found', 'path': found})
        elif entry['source'].get('git_url'):
            items.append({'kind': 'plugin', 'name': entry['pack'], 'status': 'installable',
                          'source': entry['source']['git_url']})
        else:
            items.append({'kind': 'plugin', 'name': 'entry["pack"]', 'status': 'manual'})

    # 模型：多根递归搜索，缺失只列清单不自动下载；optional 项（默认旁路等）缺失仅警告
    optional_names = {m['filename'] for m in manifest['models'] if m.get('optional')}
    for m in manifest['models']:
        hit = _find_model(cfg, m)
        is_opt = m['filename'] in optional_names
        items.append({'kind': 'model', 'name': m['filename'], 'role': m['role'],
                      'status': 'found' if hit else ('warn' if is_opt else 'missing'),
                      'optional': is_opt, 'path': hit})

    # 快照
    snap_ok = os.path.isdir(os.path.join(bootstrap.SNAP, 'comfy'))
    items.append({'kind': 'snapshot', 'name': 'engine_snapshot',
                  'status': 'found' if snap_ok else 'missing', 'path': bootstrap.SNAP})

    summary = {
        'ok': all(i['status'] in ('found', 'ok', 'installable', 'warn') for i in items if i['kind'] != 'plugin')
              and all(i['status'] != 'missing' for i in items if i['kind'] == 'runtime'),
        'missing': [i['name'] for i in items if i['status'] == 'missing'],
        'warn': [i['name'] for i in items if i['status'] == 'warn'],
        'installable': [i['name'] for i in items if i['status'] == 'installable'],
        'items': items,
    }
    return summary


def _find_pack(cfg, entry) -> str | None:
    for d in entry['detect'].get('dirs', []):
        p = os.path.join(cfg.get('comfyui_dir') or '', 'custom_nodes', d)
        if os.path.isdir(p):
            return p
        p = os.path.join(bootstrap.PLUGIN_DIR, d)
        if os.path.isdir(p):
            return p
    return None


def _find_model(cfg, m) -> str | None:
    import glob
    roots = cfg.get('model_dirs') or []
    if m['role'] == 'input_image':
        roots = [cfg.get('input_dir') or '']
    for root in roots:
        hits = glob.glob(os.path.join(root, '**', m['filename']), recursive=True)
        if hits:
            return hits[0]
    return None


if __name__ == '__main__':
    print(json.dumps(scan(), ensure_ascii=False, indent=1))
