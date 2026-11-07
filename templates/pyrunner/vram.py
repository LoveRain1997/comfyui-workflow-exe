# -*- coding: utf-8 -*-
"""显存策略：一次/连续 统一调度（§6.7）。

- 应用级唯一开关；图内卸载类节点在执行期按模式覆写（不双清、不漏清）。
- 统一卸载入口 = mm.unload_all_models()（llama 钩子已 patch，双卸载）+ gc + empty_cache。
"""
import logging

log = logging.getLogger('runner.vram')


def apply_mode(executor, manifest, mode: str) -> None:
    """按显存模式覆写 manifest 里的 vram_sync_nodes。mode ∈ {'continuous','once'}。"""
    for entry in manifest.get('vram_sync_nodes', []):
        want = entry.get(mode)   # once→true/active; continuous→false/bypass
        if entry.get('kind') == 'widget_value':
            executor.set_value(entry['node_id'], entry['field'], want)
            for extra in entry.get('also_nodes', []):
                executor.set_value(extra, entry['field'], want)
        elif entry.get('kind') == 'node_state':
            executor.set_state(entry['node_id'], want)
            for extra in entry.get('also_nodes', []):
                executor.set_state(extra, want)
        else:
            log.warning('未知 vram_sync kind: %s', entry)


def after_run(executor, mode: str) -> dict:
    """跑完后：once=统一卸载+清缓存；continuous=保留驻留。返回 VRAM 采样。"""
    info = {'mode': mode}
    if mode == 'once':
        executor.unload_all()
        executor.clear_cache()
    info['vram_free_mb'] = _vram_free_mb()
    return info


def vram_free_mb():
    """通用功能：显存空闲采样（UI 状态行 / RESULT 内存段共用）。"""
    try:
        import torch
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info()
            return round(free / 1048576)
    except Exception:
        pass
    return None


def _vram_free_mb():
    return vram_free_mb()
