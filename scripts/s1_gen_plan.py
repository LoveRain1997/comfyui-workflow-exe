# -*- coding: utf-8 -*-
"""S1 机械对位：widgets_values × INPUT_TYPES + Reroute 塌缩 + 官方 extra 初始化 → graph_plan.json。
要求程序根已有 engine_snapshot（先跑 s3_new_program.py）。
"""
import argparse, json, os, sys, importlib, asyncio, contextlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--workflow', required=True)
ap.add_argument('--program', required=True)
ap.add_argument('--comfyui', required=True, help='ComfyUI 根目录')
ap.add_argument('--manifest', default=None, help='deps_manifest.json 路径（插件包导入；S1D 先写 manifest 再跑本脚本）')
ap.add_argument('--fix-bypass', default='', help='分支冻结：强制旁路节点id，逗号分隔（多exe原则）')
ap.add_argument('--fix-activate', default='', help='分支冻结：强制激活节点id，逗号分隔')
ap.add_argument('--fix-value', default='', help='分支冻结：节点/字段=值，分号分隔（如 79/value=2）')
a = ap.parse_args()

SNAP = os.path.join(a.program, 'engine_snapshot')
sys.path.insert(0, SNAP)
sys.path.insert(0, a.comfyui)

import types
from unittest.mock import MagicMock
_srv = types.ModuleType('server'); _P = MagicMock(); _P.instance = MagicMock()
_srv.PromptServer = _P; sys.modules['server'] = _srv
devnull = open(os.devnull, 'w', encoding='utf-8', errors='replace')
def quiet(fn, *k, **kw):
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        return fn(*k, **kw)

import nodes as _core
async def _init():
    await _core.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False)
quiet(asyncio.run, _init())
CLASSES = {k: ('core/extras', v) for k, v in _core.NODE_CLASS_MAPPINGS.items()}

manifest_p = a.manifest or os.path.join(a.program, 'deps_manifest.json')
if os.path.isfile(manifest_p):
    sys.path.insert(0, os.path.join(a.comfyui, 'custom_nodes'))
    for entry in json.load(open(manifest_p, encoding='utf-8')).get('custom_nodes', []):
        try:
            m = quiet(importlib.import_module, entry['pack'])
            CLASSES.update({k: (entry['pack'], v) for k, v in getattr(m, 'NODE_CLASS_MAPPINGS', {}).items()})
        except Exception as e:
            print(f"PACK_FAIL {entry['pack']}: {type(e).__name__}: {e}")
else:
    sys.path.insert(0, os.path.join(a.comfyui, 'custom_nodes'))

def class_info(cls):
    it = quiet(cls.INPUT_TYPES)
    fields = []
    for sect in ('required', 'optional'):
        for k, v in (it.get(sect) or {}).items():
            if isinstance(v, (list, tuple)) and v:
                t = v[0]
                if isinstance(t, list):
                    t = 'COMBO'
                cfg = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
            else:
                t, cfg = str(v), {}
            fields.append((k, t, cfg, sect))
    return fields

WIDGET_TYPES = {'INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO'}
SEED_CONTROLS = {'fixed', 'increment', 'decrement', 'randomize'}

wf = json.load(open(a.workflow, encoding='utf-8'))
nodes = {n['id']: n for n in wf['nodes']}
links = {l[0]: list(l[:6]) for l in wf.get('links', [])}

def follow_source(node_id, slot):
    n = nodes[node_id]
    while n.get('type') == 'Reroute':
        inp = next((i for i in (n.get('inputs') or []) if i.get('link') is not None), None)
        if inp is None:
            return None, None
        l = links[inp['link']]
        node_id, slot = l[1], l[2]
        n = nodes[node_id]
    return node_id, slot

plan_nodes, report = {}, {'dropped': [], 'warnings': [], 'missing': []}
for nid in sorted(nodes):
    n = nodes[nid]
    t = n.get('type')
    has_out = any(l[1] == nid for l in links.values())
    if t == 'Reroute' or (t not in CLASSES and not has_out):
        report['dropped'].append(f'{nid} {t}')
        continue
    if t not in CLASSES:
        report['missing'].append(f'{nid} {t}')
        continue
    pack, cls = CLASSES[t]
    fields = class_info(cls)
    linked = {i['name']: i['link'] for i in (n.get('inputs') or []) if i.get('link') is not None}
    wvals = list(n.get('widgets_values') or [])
    inputs, control = {}, {}
    wi = 0
    for k, ty, cfg, sect in fields:
        slot_consumer = (ty in WIDGET_TYPES and not (ty == 'STRING' and cfg.get('lazy'))) or 'UPLOAD' in ty
        storable = ty in WIDGET_TYPES and k not in linked
        if not slot_consumer:
            continue
        if wi >= len(wvals):
            if storable:
                report['warnings'].append(f'{nid} {t}: no widget value for {k} (用默认)')
            continue
        v = wvals[wi]; wi += 1
        if storable:
            inputs[k] = {'v': v}
        if 'seed' in k and wi < len(wvals) and isinstance(wvals[wi], str) and wvals[wi] in SEED_CONTROLS:
            control[k] = wvals[wi]; wi += 1
    if wi < len(wvals):
        report['warnings'].append(f'{nid} {t}: {len(wvals)-wi} unconsumed: {wvals[wi:]}')
    for name, lid in linked.items():
        l = links[lid]
        src, sslot = follow_source(l[1], l[2])
        if src is None:
            report['warnings'].append(f'{nid}.{name}: 空链')
            continue
        inputs[name] = {'l': [src, sslot]}
    plan_nodes[str(nid)] = {'class_type': t, 'pack': pack,
                       'state': 'bypass' if n.get('mode') == 4 else 'active',
                       'outputs': len(getattr(cls, 'RETURN_TYPES', ()) or ()),
                       'output_node': bool(getattr(cls, 'OUTPUT_NODE', False)),
                       'function': getattr(cls, 'FUNCTION', None),
                       'inputs': inputs, 'control': control}

# 分支冻结（多exe原则）：先定状态再产出
for nid_s in [x.strip() for x in a.fix_bypass.split(',') if x.strip()]:
    if nid_s in plan_nodes:
        plan_nodes[nid_s]['state'] = 'bypass'
for nid_s in [x.strip() for x in a.fix_activate.split(',') if x.strip()]:
    if nid_s in plan_nodes:
        plan_nodes[nid_s]['state'] = 'active'

# 分支冻结：值层（档位值等）
for pair in [x.strip() for x in a.fix_value.split(';') if x.strip()]:
    nid_s, _, field = pair.partition('/')
    k, _, v = field.partition('=')
    if nid_s in plan_nodes and k:
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
        plan_nodes[nid_s]['inputs'][k] = {'v': v}

lazy = {}
for nid, pn in plan_nodes.items():
    if 'indexswitch' in pn['class_type'].lower():
        texts = sorted([k for k in pn['inputs'] if k.startswith('text')],
                       key=lambda s: int(''.join(ch for ch in s if ch.isdigit()) or 0))
        lazy[nid] = {'index_input': 'index', 'texts': texts}

json.dump({'version': 2, 'workflow': os.path.basename(a.workflow), 'nodes': plan_nodes,
           'lazy_switches': lazy},
          open(os.path.join(a.program, 'graph_plan.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
report['n_nodes'] = len(plan_nodes)
print(json.dumps(report, ensure_ascii=False, indent=1))
