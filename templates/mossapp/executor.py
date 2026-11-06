# -*- coding: utf-8 -*-
"""迷你图执行器：拓扑执行、懒开关、旁路透传、输出缓存（模型常驻复用）。

设计要点：
- 输出缓存键 = 节点全部入参指纹（含上游指纹）→ 装载类节点入参不变即复用，二轮不重载。
- lazy 开关（easy textIndexSwitch）：只解析被选中的 text 分支 → 未选分支（LLM）不执行不加载。
- 旁路节点：输出 = 上游链路透传（不缓存，上游自身有缓存）。
"""
import json
import logging
import os

from . import bootstrap

log = logging.getLogger('mossapp.exec')


class Executor:
    def __init__(self, registry):
        self.reg = registry
        with open(os.path.join(bootstrap.PROG, 'graph_plan.json'), encoding='utf-8') as f:
            self.plan = json.load(f)
        self.nodes = self.plan['nodes']
        self.lazy = self.plan.get('lazy_switches', {})
        self.cache = {}        # node_id -> (fingerprint, outputs)
        self.instances = {}    # node_id -> 节点实例
        self.progress = None   # fn(done, total, node_type)

    # ---------- 参数写入 ----------
    def set_value(self, node_id, field, value):
        self.nodes[str(node_id)]['inputs'][field] = {'v': value}
        self.cache.pop(str(node_id), None)

    def set_state(self, node_id, state):
        self.nodes[str(node_id)]['state'] = state
        self.cache.pop(str(node_id), None)

    # ---------- 执行（需求驱动：只有被输出节点递归需要的节点才执行） ----------
    def run(self):
        output_ids = [nid for nid, n in self.nodes.items() if n.get('output_node')]
        est = self._walk_estimate(output_ids)
        self._done_ids = set()
        self._total = max(1, len(est))
        results = {}
        for nid in output_ids:
            results[nid] = self._node_output(nid, set())[0]
        if self.progress:
            self.progress(self._total, self._total, '完成')
        return results

    def _walk_estimate(self, ids):
        seen, stack = set(), list(ids)
        while stack:
            nid = stack.pop()
            if nid in seen or nid not in self.nodes:
                continue
            seen.add(nid)
            for spec in self.nodes[nid]['inputs'].values():
                if 'l' in spec:
                    stack.append(str(spec['l'][0]))
        return seen

    def _tick(self, nid):
        if self.progress:
            self._done_ids.add(nid)
            self.progress(len(self._done_ids), self._total, self.nodes[nid]['class_type'])

    # ---------- 指纹与缓存 ----------
    def _fingerprint(self, nid, guard):
        node = self.nodes[nid]
        parts = [nid, node['class_type'], node['state']]
        for name in sorted(node['inputs']):
            spec = node['inputs'][name]
            if 'v' in spec:
                parts.append((name, repr(spec['v'])[:200]))
            else:
                src = str(spec['l'][0])
                if src in guard:
                    parts.append((name, f'cycle:{src}'))
                    continue
                guard.add(src)
                try:
                    parts.append((name, (self._fingerprint(src, guard), spec['l'][1])))
                finally:
                    guard.discard(src)
        return hash(tuple(parts))

    # ---------- 懒开关 ----------
    def _lazy_selected_field(self, nid):
        meta = self.lazy[nid]
        idx = self._input_value(nid, meta['index_input'])
        try:
            idx = int(idx) if idx is not None else 0
        except (TypeError, ValueError):
            idx = 0
        node = self.nodes[nid]
        if 0 <= idx < len(meta['texts']) and meta['texts'][idx] in node['inputs']:
            return meta['texts'][idx]
        for f in reversed(meta['texts']):
            if f in node['inputs']:
                return f
        return None

    def _input_value(self, nid, field):
        spec = self.nodes[nid]['inputs'].get(field)
        if spec is None:
            return None
        if 'v' in spec:
            return spec['v']
        return self._node_output(str(spec['l'][0]), {nid})[0][spec['l'][1]]

    # ---------- 节点求值 ----------
    def _node_output(self, nid, guard):
        node = self.nodes[nid]
        if node['state'] == 'bypass':
            return self._passthrough(node, guard), None

        fp = self._fingerprint(nid, {nid} | guard)
        hit = self.cache.get(nid)
        if hit and hit[0] == fp:
            return hit[1], hit[1]

        if nid in self.lazy:
            kwargs = self._gather_kwargs_lazy(nid, guard)
        else:
            kwargs = self._gather_kwargs(nid, guard)
        cls = self.reg.classes[node['class_type']][1]
        fn_name = node.get('function') or 'EXECUTE_NORMALIZED'
        if nid not in self.instances:
            self.instances[nid] = cls()
        fn = getattr(self.instances[nid], fn_name)
        out = None
        import torch
        with torch.no_grad():   # 对齐 ComfyUI 执行语义：节点函数一律 no_grad
            out = fn(**kwargs)
        outputs = _unwrap(out)
        self.cache[nid] = (fp, outputs)
        self._tick(nid)
        return outputs, outputs

    def _gather_kwargs(self, nid, guard):
        kwargs, autogrow = {}, {}
        for name, spec in self.nodes[nid]['inputs'].items():
            if 'v' in spec:
                kwargs[name] = spec['v']
                continue
            src, slot = spec['l']
            if str(src) in guard:
                raise RuntimeError(f'环依赖 {nid}<-{src} ({name})')
            guard.add(str(src))
            try:
                outs = self._node_output(str(src), guard)[0]
            finally:
                guard.discard(str(src))
            val = outs[slot] if outs and slot < len(outs) else None
            if '.' in name:   # autogrow: prefix.N -> kwargs[prefix]=list
                prefix, suffix = name.split('.', 1)
                autogrow.setdefault(prefix, [])
                try:
                    autogrow[prefix].append((int(suffix.rsplit('_', 1)[-1]), val))
                except ValueError:
                    autogrow[prefix].append((0, val))
            else:
                kwargs[name] = val
        for prefix, items in autogrow.items():
            kwargs[prefix] = [v for _, v in sorted(items)]
        return kwargs

    def _gather_kwargs_lazy(self, nid, guard):
        """懒开关：只解析 index + 被选中的 text 分支，未选分支（如 LLM）不执行。"""
        node = self.nodes[nid]
        picked = self._lazy_selected_field(nid)
        kwargs = {}
        for name, spec in node['inputs'].items():
            if 'v' in spec:
                kwargs[name] = spec['v']
        kwargs['index'] = self._input_value(nid, self.lazy[nid]['index_input'])
        if picked:
            spec = node['inputs'][picked]
            src, slot = spec['l']
            kwargs[picked] = self._node_output(str(src), guard)[0][slot]
        return kwargs

    def _passthrough(self, node, guard):
        for name, spec in node['inputs'].items():
            if 'l' in spec:
                src, slot = spec['l']
                outs = self._node_output(str(src), guard)[0]
                return outs if outs else (None,)
        return (None,)

    # ---------- 显存 ----------
    def unload_all(self):
        import gc
        import torch
        try:
            import comfy.model_management as mm
            mm.unload_all_models()   # llama 钩子已 patch：连本地 LLM 一起卸载
        except Exception as e:  # noqa
            log.warning('unload_all_models: %s', e)
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

    def clear_cache(self):
        self.cache.clear()
        self.instances.clear()

    def randomize_seed(self, node_id=31, field='seed'):
        self.set_value(node_id, field, random.getrandbits(53))


def _unwrap(out):
    """统一输出：元组 / dict(result=) / io.NodeOutput。"""
    if out is None:
        return (None,)
    if isinstance(out, dict):
        result = out.get('result', ())
        return tuple(result) if isinstance(result, (list, tuple)) else (result,)
    result = getattr(out, 'result', None)
    if result is not None:
        return tuple(result) if isinstance(result, (list, tuple)) else (result,)
    if isinstance(out, tuple):
        return out
    return (out,)
