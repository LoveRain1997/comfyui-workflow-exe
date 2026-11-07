# -*- coding: utf-8 -*-
"""CLI 入口（AI 友好）：scan / schema / run / ui。

exit codes: 0 成功 | 2 缺依赖 | 3 缺模型 | 4 运行时错误 | 5 环境不可用
"""
import sys
import json
import argparse
import logging
import threading


def main(argv=None):
    # 子进程/管道环境下 stdio 默认 locale(GBK)，含 emoji 的插件横幅会炸（坑34）→ 强制 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s',
                        filename=None)  # stdout；UI 模式走文件
    p = argparse.ArgumentParser(prog='runner', description='工作流 JSON → 独立出图程序')
    sub = p.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('scan', help='环境扫描三色报告')
    sp.add_argument('--json', action='store_true')
    sub.add_parser('schema', help='打印 params_schema.json')
    rp = sub.add_parser('run', help='执行出图')
    rp.add_argument('--params', help='参数 JSON 文件（键=params_schema 的 id）')
    rp.add_argument('--set', action='append', default=[], help='key=value 单参数覆盖，可多次')
    rp.add_argument('--seed', type=int)
    rp.add_argument('--count', type=int)
    rp.add_argument('--mode', type=int, choices=[1, 2, 3])
    rp.add_argument('--vram-mode', choices=['continuous', 'once'])
    rp.add_argument('--output')
    rp.add_argument('--progress', action='store_true',
                    help='进度行 PROGRESS i/t name + 结尾 RESULT 行（UI 子进程模式）')
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

    if args.cmd == 'run':
        return _run(args)
    if args.cmd == 'ui':
        from .ui import run_ui
        return run_ui()
    if args.cmd == 'serve':
        return _serve(args)
    return 0


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


def _run(args):
    from . import bootstrap
    from .runner import Runner, default_params
    from .scan import scan

    rep = scan()
    hard_missing = [m for m in rep['missing']]   # optional 模型缺失已是 warn，不进 missing
    if hard_missing:
        print(json.dumps({'error': 'missing', 'missing': hard_missing}, ensure_ascii=False, indent=1))
        return 3

    reg = bootstrap.bootstrap()
    params = default_params()
    if args.params:
        with open(args.params, encoding='utf-8') as f:
            params.update(json.load(f))
    for kv in args.set:
        k, _, v = kv.partition('=')
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            pass
        params[k] = v
    if args.seed is not None:
        params['seed'] = args.seed
    if args.count is not None:
        params['batch'] = args.count
    if args.mode is not None:
        params['mode'] = args.mode
    if args.vram_mode:
        params['vram_mode'] = args.vram_mode
    if args.output:
        params['_output_dir'] = args.output
        reg.cfg['output_dir'] = args.output

    runner = Runner(reg)

    def _progress(done, total, name):
        if getattr(args, 'progress', None):
            print(f'PROGRESS {done} {total} {name}', flush=True)

    try:
        result = runner.run(params, _progress)
        print(json.dumps({'ok': True, 'files': result['files'],
                          'seconds': result['seconds'], 'memory': result['memory']},
                         ensure_ascii=False, indent=1))
        if getattr(args, 'progress', None):
            print('RESULT ' + json.dumps({'ok': True, 'files': result['files'],
                                          'seconds': result['seconds'],
                                          'memory': result['memory']}, ensure_ascii=False), flush=True)
        return 0
    except Exception as e:  # noqa
        import traceback
        traceback.print_exc()
        if getattr(args, 'progress', None):
            print('RESULT ' + json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), flush=True)
        return 4


if __name__ == '__main__':
    sys.exit(main())
