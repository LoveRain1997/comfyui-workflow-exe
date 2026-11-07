# -*- coding: utf-8 -*-
"""S0 侦察：工作流节点清单 + venv 实测 INPUT_TYPES + 插件/模型/VRAM 线索。只读。"""
import argparse, json, os, sys, re, glob, importlib, contextlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--workflow', required=True)
ap.add_argument('--comfyui', required=True, help='ComfyUI 根目录')
ap.add_argument('--out', default=None)
a = ap.parse_args()

sys.path.insert(0, a.comfyui)
sys.path.insert(0, os.path.join(a.comfyui, 'custom_nodes'))

import types
from unittest.mock import MagicMock
_srv = types.ModuleType('server'); _P = MagicMock(); _P.instance = MagicMock()
_srv.PromptServer = _P; sys.modules['server'] = _srv

devnull = open(os.devnull, 'w', encoding='utf-8', errors='replace')
def quiet(fn, *k, **kw):
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        return fn(*k, **kw)

wf = json.load(open(a.workflow, encoding='utf-8'))
nodes = {n['id']: n for n in wf['nodes']}
links = {l[0]: list(l[:6]) for l in wf.get('links', [])}

def it_entry(v):
    if isinstance(v, (list, tuple)) and v:
        t = v[0]; cfg = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
        if isinstance(t, list): return {'type': 'COMBO', 'choices': t, 'default': cfg.get('default')}
        if t == 'COMBO': return {'type': t, 'choices': cfg.get('choices') or cfg.get('options'), 'default': cfg.get('default')}
        return {'type': t if isinstance(t, str) else str(t), 'default': cfg.get('default'),
                'min': cfg.get('min'), 'max': cfg.get('max'), 'step': cfg.get('step')}
    return {'type': str(v)}

cls_index = {}
packs = {}
for d in sorted(os.listdir(os.path.join(a.comfyui, 'custom_nodes'))):
    p = os.path.join(a.comfyui, 'custom_nodes', d)
    if not os.path.isdir(p) or d.startswith('__') or d.endswith('.example'):
        continue
    try:
        m = quiet(importlib.import_module, d)
        mp = getattr(m, 'NODE_CLASS_MAPPINGS', {})
        cls_index.update({k: (d, v) for k, v in mp.items()})
        packs[d] = {'ok': True, 'classes': len(mp)}
    except Exception as e:
        packs[d] = {'ok': False, 'err': f'{type(e).__name__}: {e}'}

out_nodes = []
for n in sorted(wf['nodes'], key=lambda x: x['id']):
    t = n.get('type')
    e = {'id': n['id'], 'type': t, 'mode': n.get('mode'), 'title': n.get('title'),
         'widgets_values': n.get('widgets_values'),
         'linked_in': [i['name'] for i in (n.get('inputs') or []) if i.get('link') is not None],
         'widget_in': [i['name'] for i in (n.get('inputs') or []) if i.get('widget')]}
    pack, cls = cls_index.get(t, (None, None))
    e['pack'] = pack
    if cls is not None:
        try:
            it = quiet(cls.INPUT_TYPES)
            e['input_types'] = {
                'required': {k: it_entry(v) for k, v in (it.get('required') or {}).items()},
                'optional': {k: it_entry(v) for k, v in (it.get('optional') or {}).items()},
                'hidden': list((it.get('hidden') or {}).keys())}
        except Exception as ex:
            e['input_types_error'] = f'{type(ex).__name__}: {ex}'
    out_nodes.append(e)

pat = re.compile(r'clean|free|unload|offload|purge|release|vram|gpu|memory', re.I)
vram_classes = {k: d for k, (d, _) in cls_index.items() if pat.search(k)}

model_refs = {}
for e in out_nodes:
    for wv in (e.get('widgets_values') or []):
        if isinstance(wv, str) and wv.endswith(('.safetensors', '.sft', '.pt', '.ckpt', '.gguf', '.pth', '.bin')):
            model_refs.setdefault(wv, []).append(e['id'])

models_root = os.path.join(a.comfyui, 'models')
model_exist = {}
for wv in model_refs:
    hits = glob.glob(os.path.join(models_root, '**', os.path.basename(wv)), recursive=True)
    model_exist[wv] = {'exists': bool(hits), 'hits': hits[:3]}

rep = {'workflow': a.workflow, 'wf_version': wf.get('version'), 'nodes_total': len(wf['nodes']),
       'packs': packs, 'nodes': out_nodes, 'vram_classes': vram_classes,
       'model_refs': model_refs, 'model_exist': model_exist,
       'links': [list(l[:6]) for l in wf.get('links', [])], 'groups': wf.get('groups')}
txt = json.dumps(rep, ensure_ascii=False, indent=1, default=str)
if a.out:
    open(a.out, 'w', encoding='utf-8').write(txt)
print(txt if not a.out else f'report -> {a.out}')
