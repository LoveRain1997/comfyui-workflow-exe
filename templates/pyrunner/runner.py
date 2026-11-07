# -*- coding: utf-8 -*-
"""运行器：params 注入 → 执行 → 产物收集 + sidecar 元数据。"""
import json
import os
import time
import random
import logging

from . import bootstrap, vram

log = logging.getLogger('runner.runner')


def load_schema() -> dict:
    with open(os.path.join(bootstrap.PROG, 'params_schema.json'), encoding='utf-8') as f:
        return json.load(f)


def default_params() -> dict:
    """schema 默认值 + graph_plan 里的工作流原值（default_from）。"""
    import json as _json
    plan = _json.load(open(os.path.join(bootstrap.PROG, 'graph_plan.json'), encoding='utf-8'))
    schema = load_schema()
    params = {'vram_mode': None}
    for item in schema['core']:
        _collect(item, params, plan)
    for item in schema['advanced']:
        _collect(item, params, plan)
    return params


def _collect(item, params, plan):
    if item.get('control') == 'group' or item.get('bindings'):
        for b in item.get('bindings', []):
            params[b['id']] = b.get('default', _from_plan(plan, b.get('node'), b.get('field')))
        if item.get('default') and isinstance(item['default'], dict):
            for k, v in item['default'].items():
                params.setdefault(k, v)
        return
    if item.get('app_param'):
        params[item['id']] = item.get('default')
        return
    if 'default' in item:
        params[item['id']] = item['default']
    elif 'default_from' in item:
        d = item['default_from']
        params[item['id']] = _from_plan(plan, d['node'], d['field'])
    if item.get('field_prefix'):   # group50
        node = str(item['node'])
        inputs = plan['nodes'][node]['inputs']
        for f in item['fields']:
            if f in inputs and 'v' in inputs[f]:
                params[f] = inputs[f]['v']


def _from_plan(plan, node, field):
    if node is None or field is None:
        return None
    spec = plan['nodes'][str(node)]['inputs'].get(field)
    return spec['v'] if spec and 'v' in spec else None


class Runner:
    def __init__(self, registry):
        self.reg = registry
        self.schema = load_schema()
        self.ex = None

    def _ensure_executor(self):
        if self.ex is None:
            from .executor import Executor
            self.ex = Executor(self.reg)
        return self.ex

    def apply_params(self, ex, params: dict):
        """按 schema binding 把 params 写进 plan。分支冻结在生成期（graph_plan --fix-*），
        CLI/UI 无分支解冻后门（不得硬编码节点 id 注入）。"""
        if params.get('seed') is not None:
            seed = int(params['seed'])
            if seed < 0:
                seed = random.getrandbits(53)
            ex.set_value(31, 'seed', seed)
            ex.applied_seed = seed
        for item in self.schema['core']:
            _apply_item(ex, item, params)
        for item in self.schema['advanced']:
            _apply_item(ex, item, params)

    def run(self, params: dict, progress=None) -> dict:
        ex = self._ensure_executor()
        ex.progress = progress
        self.apply_params(ex, params)
        vram.apply_mode(ex, self.reg.manifest, params.get('vram_mode') or 'continuous')
        t0 = time.time()
        results = ex.run()
        files = self._collect_output(params, t0)
        mem = vram.after_run(ex, params.get('vram_mode') or 'continuous')
        return {'files': files, 'seconds': round(time.time() - t0, 1), 'memory': mem}

    def release_vram(self):
        """通用功能：独立「释放显存」按钮。只卸载显存权重，不清执行缓存
        （模型对象仍有效，ComfyUI 语义下按需自动重载），不影响 continuous 模式。"""
        ex = self._ensure_executor()
        ex.unload_all()
        return self.vram_free_mb()

    def vram_free_mb(self):
        return vram.vram_free_mb()

    def _collect_output(self, params, t0):
        out_dir = self.reg.cfg.get('output_dir') or os.path.join(bootstrap.PROG, 'output')
        files = []
        for root, _dirs, names in os.walk(out_dir):
            for n in names:
                p = os.path.join(root, n)
                if n.lower().endswith(('.png', '.jpg', '.webp')) and os.path.getmtime(p) >= t0 - 1:
                    files.append(p)
                    self._write_sidecar(p, params)
        return sorted(files)

    def _write_sidecar(self, png, params):
        base, _ = os.path.splitext(png)
        side = base + '.params.json'
        if os.path.exists(side):
            return
        data = dict(params)
        data['_seed_used'] = getattr(self.ex, 'applied_seed', None)
        data['_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(side, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError as e:
            log.warning('sidecar 写入失败: %s', e)


def _apply_item(ex, item, params):
    if item.get('app_param'):
        return
    if item.get('bindings'):
        for b in item['bindings']:
            v = params.get(b['id'])
            if v is None:
                continue
            if b.get('field') == '__state__':   # 旁路启用开关
                ex.set_state(b['node'], 'active' if v else 'bypass')
            else:
                ex.set_value(b['node'], b['field'], v)
                for extra in b.get('also_nodes', []):
                    ex.set_value(extra, b['field'], v)
        return
    b = item.get('binding')
    if b:
        v = params.get(item['id'])
        if v is not None:
            ex.set_value(b['node'], b['field'], v)
            for extra in item.get('also_nodes', []):
                ex.set_value(extra, b['field'], v)
    if item.get('field_prefix'):   # group50 权重
        for f in item['fields']:
            v = params.get(f)
            if v is not None:
                ex.set_value(item['node'], f, v)
