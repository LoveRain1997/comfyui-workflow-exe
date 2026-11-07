# -*- coding: utf-8 -*-
"""CLI 入口（AI 友好契约）：scan / schema / describe / run / outputs / serve / ui。

契约：
- stdout = 机器可读（JSON / PROGRESS / RESULT 行）；stderr = 人类日志（logging）
- exit codes: 0 成功 | 2 缺依赖 | 3 缺模型 | 4 运行时/参数错误 | 5 环境不可用
- 未知参数键 fail-fast（列出合法键，不静默忽略）
- 分支冻结在 graph_plan（生成期 --fix-*），CLI 不提供分支解冻后门
"""
import os
import sys
import json
import argparse
import logging


def main(argv=None):
    # 子进程/管道环境下 stdio 默认 locale(GBK)，含 emoji 的插件横幅会炸（坑34）→ 强制 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s',
                        filename=None)  # stderr=人类日志；stdout=机器输出
    p = argparse.ArgumentParser(prog='runner', description='工作流 JSON → 独立出图程序（AI 友好 CLI）')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('scan', help='环境扫描三色报告（JSON）')
    sub.add_parser('schema', help='打印 params_schema.json')
    sub.add_parser('describe', help='一次性给全 AI 所需：schema+defaults+config+scan+示例命令')
    rp = sub.add_parser('run', help='执行出图')
    rp.add_argument('--params', help='参数 JSON 文件（键=params_schema 的 id）')
    rp.add_argument('--set', action='append', default=[], help='key=value 单参数覆盖，可多次（未知键报错）')
    rp.add_argument('--seed', type=int)
    rp.add_argument('--count', type=int)
    rp.add_argument('--vram-mode', choices=['continuous', 'once'])
    rp.add_argument('--output')
    rp.add_argument('--json', action='store_true', help='stdout 仅输出单行 JSON 结果（含错误/traceback）')
    rp.add_argument('--progress', action='store_true',
                    help='进度行 PROGRESS i/t name + 结尾 RESULT 行（UI 子进程模式）')
    op = sub.add_parser('outputs', help='列出产物（最新优先，含 sidecar seed）')
    op.add_argument('--last', type=int, default=10)
    op.add_argument('--dir', help='覆盖输出目录')
    sub.add_parser('ui', help='启动图形界面（qfluentwidgets）')
    sub.add_parser('serve', help='常驻推理服务（UI 子进程专用，stdin=params JSON 行）')
    args = p.parse_args(argv)

    if args.cmd == 'scan':
        from .scan import scan
        rep = scan()
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep['ok'] else (3 if rep['missing'] else 2)

    if args.cmd == 'schema':
        from .runner import load_schema
        print(json.dumps(load_schema(), ensure_ascii=False, indent=1))
        return 0

    if args.cmd == 'describe':
        return _describe()
    if args.cmd == 'outputs':
        return _outputs(args)
    if args.cmd == 'run':
        return _run(args)
    if args.cmd == 'ui':
        from .ui import run_ui
        return run_ui()
    if args.cmd == 'serve':
        return _serve(args)
    return 0


def _describe():
    """AI 引导命令：一次给全驱动本程序所需的一切。"""
    from . import bootstrap
    from .runner import load_schema, default_params
    from .scan import scan
    cfg = bootstrap.load_config()
    schema = load_schema()
    valid_keys = set()
    for item in schema.get('core', []) + schema.get('advanced', []):
        valid_keys.add(item['id'])
        for b in item.get('bindings', []):
            valid_keys.add(b['id'])
    valid_keys |= {'vram_mode', '_output_dir'}
    rep = {
        'app': schema.get('app'),
        'schema': schema,
        'defaults': default_params(),
        'config': cfg,
        'scan': scan(),
        'valid_param_keys': sorted(valid_keys),
        'cli': {
            'run': 'runner run --params <json> [--set k=v ...] [--seed N] [--count N] '
                   '[--vram-mode continuous|once] [--output DIR] [--json] [--progress]',
            'exit_codes': {'0': '成功', '2': '缺依赖', '3': '缺模型', '4': '运行时/参数错误', '5': '环境不可用'},
            'note': 'stdout=机器输出；stderr=人类日志；分支冻结在生成期（graph_plan），CLI 无分支后门',
        },
    }
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 0


def _outputs(args):
    from . import bootstrap
    cfg = bootstrap.load_config()
    out_dir = args.dir or cfg.get('output_dir') or ''
    items = []
    if os.path.isdir(out_dir):
        for f in sorted(os.listdir(out_dir)):
            fp = os.path.join(out_dir, f)
            if f.lower().endswith(('.png', '.jpg', '.webp')):
                side = os.path.splitext(fp)[0] + '.params.json'
                seed, mtime_cfg = None, None
                if os.path.isfile(side):
                    try:
                        sd = json.load(open(side, encoding='utf-8'))
                        seed = sd.get('_seed_used')
                        mtime_cfg = sd.get('_time')
                    except Exception:  # noqa
                        pass
                items.append({'file': fp, 'seed': seed, 'sidecar_time': mtime_cfg,
                              'mtime': os.path.getmtime(fp)})
    items.sort(key=lambda x: -x['mtime'])
    items = items[:max(1, args.last)]
    print(json.dumps({'output_dir': out_dir, 'count': len(items), 'items': items},
                     ensure_ascii=False, indent=1))
    return 0


def _valid_param_keys(schema):
    valid = set()
    for item in schema.get('core', []) + schema.get('advanced', []):
        valid.add(item['id'])
        for b in item.get('bindings', []):
            valid.add(b['id'])
    valid |= {'vram_mode', '_output_dir'}
    return valid


def _run(args):
    from . import bootstrap
    from .runner import Runner, load_schema, default_params
    from .scan import scan

    schema = load_schema()
    valid_keys = _valid_param_keys(schema)

    def _emit(payload, result_line=False):
        """机器输出：--json 时 stdout 单行 JSON；否则人类可读 indent JSON。"""
        line = json.dumps(payload, ensure_ascii=False)
        if getattr(args, 'json', None) or result_line:
            print(('RESULT ' if result_line else '') + line, flush=True)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=1))

    # 参数校验前置（fail-fast）：不加载 40 秒的推理环境
    params = default_params()
    if args.params:
        with open(args.params, encoding='utf-8') as f:
            incoming = json.load(f)
        unknown = [k for k in incoming if k not in valid_keys]
        if unknown:
            _emit({'ok': False, 'error': f'未知参数键: {unknown}',
                   'valid_keys': sorted(valid_keys)})
            return 4
        params.update(incoming)
    for kv in args.set:
        k, _, v = kv.partition('=')
        if k and k not in valid_keys:
            _emit({'ok': False, 'error': f'未知参数键: {k}', 'valid_keys': sorted(valid_keys)})
            return 4
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            pass
        params[k] = v
    if args.seed is not None:
        params['seed'] = args.seed
    if args.count is not None:
        params['batch'] = args.count
    if args.vram_mode:
        params['vram_mode'] = args.vram_mode

    rep = scan()
    hard_missing = [m for m in rep['missing']]   # optional 模型缺失已是 warn，不进 missing
    if hard_missing:
        _emit({'ok': False, 'error': 'missing', 'missing': hard_missing})
        return 3

    reg = bootstrap.bootstrap()
    if args.output:
        params['_output_dir'] = args.output
        reg.cfg['output_dir'] = args.output

    runner = Runner(reg)

    def _progress(done, total, name):
        if getattr(args, 'progress', None):
            print(f'PROGRESS {done} {total} {name}', flush=True)
        elif getattr(args, 'json', None):
            pass   # --json 纯净 stdout：进度只走 stderr 日志
        else:
            print(f'[{done}/{total}] {name}', flush=True)   # 默认心跳：长任务不静默

    try:
        result = runner.run(params, _progress)
        payload = {'ok': True, 'files': result['files'], 'seconds': result['seconds'],
                   'memory': result['memory'], 'output_dir': reg.cfg.get('output_dir'),
                   'log': os.path.join(bootstrap.PROG, 'runner.log')}
        _emit(payload)
        if getattr(args, 'progress', None):
            print('RESULT ' + json.dumps(payload, ensure_ascii=False), flush=True)
        return 0
    except Exception as e:  # noqa
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        payload = {'ok': False, 'error': str(e), 'traceback': tb[-2000:]}
        _emit(payload)
        if getattr(args, 'progress', None):
            print('RESULT ' + json.dumps(payload, ensure_ascii=False), flush=True)
        return 4


def _serve(args):
    """常驻推理服务（坑31）：无 UI 框架的推理进程，stdin 每行一个 params JSON，
    stdout 回 INIT/READY/PROGRESS/RESULT 行。跨请求存活 → continuous 模式模型缓存保留。"""
    from . import bootstrap
    from .runner import Runner, load_schema, default_params
    from .scan import scan
    reg = bootstrap.bootstrap()
    runner = Runner(reg)
    try:
        init_payload = {'app': load_schema().get('app'), 'schema': load_schema(),
                        'defaults': default_params(), 'config': reg.cfg, 'scan': scan()}
    except Exception as e:  # noqa
        init_payload = {'app': None, 'schema': {}, 'defaults': {}, 'config': reg.cfg,
                        'scan': {'ok': False, 'error': str(e)}}
    print('INIT ' + json.dumps(init_payload, ensure_ascii=False, default=str), flush=True)
    print('READY', flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == 'CMD release_vram':   # 通用显存功能：独立按钮，不问用户
            runner.release_vram()
            print('RESULT ' + json.dumps({'ok': True, 'action': 'release_vram',
                                          'memory': {'vram_free_mb': runner.vram_free_mb()}},
                                         ensure_ascii=False), flush=True)
            continue
        try:
            params = json.loads(line)
        except json.JSONDecodeError as e:
            print('RESULT ' + json.dumps({'ok': False, 'error': f'bad params: {e}'}, ensure_ascii=False), flush=True)
            continue

        def _progress(done, total, name):
            print(f'PROGRESS {done} {total} {name}', flush=True)

        try:
            if params.get('_output_dir'):
                reg.cfg['output_dir'] = params['_output_dir']
            result = runner.run(params, _progress)
            print('RESULT ' + json.dumps({'ok': True, 'files': result['files'],
                                          'seconds': result['seconds'],
                                          'memory': result['memory']}, ensure_ascii=False), flush=True)
        except Exception as e:  # noqa
            import traceback
            traceback.print_exc()
            print('RESULT ' + json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), flush=True)
    return 0



if __name__ == '__main__':
    sys.exit(main())
