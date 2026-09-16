"""Run separately from gunicorn. No scheduler is started by Flask imports."""
import argparse
import json
import os
import signal
import socket
import threading
import uuid
import sys
from datetime import date


def main():
    parser = argparse.ArgumentParser(description='服务端内容任务')
    parser.add_argument('command', choices=['init', 'check', 'scheduler', 'collector', 'worker', 'run', 'status', 'retry'])
    parser.add_argument('--environment', choices=['production', 'development'], default='production')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--task-id', type=int, help='run 使用的任务编号')
    parser.add_argument('--job-id', type=int, help='status / retry 使用的执行编号')
    parser.add_argument('--date', dest='edition', help='北京时间期次 YYYY-MM-DD；默认当天')
    parser.add_argument('--mode', choices=['draft', 'scheduled', 'regenerate'], default='draft', help='默认生成草稿；regenerate 使用该期保存的来源重生成；scheduled 遵守原自动发布规则')
    parser.add_argument('--reuse-cover', action='store_true', help='regenerate 时沿用该期数据库中的封面，只重新生成正文')
    parser.add_argument('--include-item-id', type=int, action='append', default=[], help='重生成时补充已核实的数据库资料编号；可重复指定，须在原采集窗口内')
    parser.add_argument('--request-id', help='手动触发幂等标识；重复使用相同标识、任务和日期返回同一个 job')
    args = parser.parse_args()
    if not os.environ.get('FLASK_POSTS_PER_PAGE') or not os.environ.get('FLASK_BBS_PER_PAGE'):
        print(json.dumps({'error': 'environment_missing', 'message': '请先加载项目 env.sh 和当前部署环境配置'}, ensure_ascii=False), file=sys.stderr)
        return 2
    from app import create_app, db
    from app.generation.configuration import utcnow, local_day, schedule_for, GenerationError
    from app.generation_models import GenerationHeartbeat
    app = create_app(os.environ.get('FLASK_CONFIG') or 'development')
    from app.generation import engine
    from app.generation.bootstrap import initialize
    from app.generation.providers import readiness
    from app.generation_models import GenerationTask, GenerationJob
    with app.app_context():
        if args.command == 'init':
            print(json.dumps(initialize(), ensure_ascii=False))
            return
        if args.command == 'check':
            print(json.dumps([{'id': t.id, 'enabled': t.enabled, 'environment': t.environment,
                'missing': readiness(json.loads(engine.task_version(t).config_json))}
                for t in GenerationTask.query.all()], ensure_ascii=False))
            return
        if args.command in ('run', 'status', 'retry'):
            try:
                if args.command != 'status':
                    if os.environ.get('CONTENT_ENVIRONMENT') != args.environment:
                        raise ValueError('CONTENT_ENVIRONMENT 必须与 --environment 一致')
                    if args.environment == 'production' and os.environ.get('FLASK_CONFIG') != 'production':
                        raise ValueError('生产任务需要 FLASK_CONFIG=production')
                if args.command == 'run':
                    task = GenerationTask.query.get(args.task_id) if args.task_id else None
                    if not task or task.environment != args.environment:
                        raise ValueError('请通过 --task-id 指定当前环境中的任务')
                    config = json.loads(engine.task_version(task).config_json)
                    now = utcnow()
                    edition = date.fromisoformat(args.edition) if args.edition else local_day(now, config)
                    if edition > local_day(now, config):
                        raise ValueError('不能生成未来期次')
                    if args.reuse_cover and args.mode != 'regenerate':
                        raise ValueError('--reuse-cover 仅用于 --mode regenerate')
                    missing = readiness(config, reuse_cover=args.reuse_cover)
                    if args.mode != 'regenerate' and not config['source_ids']:
                        missing.append('source_ids')
                    if missing:
                        raise ValueError('任务配置不完整：' + ', '.join(missing))
                    if args.mode == 'scheduled':
                        start, _, deadline = schedule_for(edition, config)
                        if not task.enabled or not start <= now <= deadline:
                            raise ValueError('任务未启用或不在自动执行窗口内；可用 --mode draft 生成草稿')
                    request_id = args.request_id or uuid.uuid4().hex
                    purpose = {'scheduled': 'scheduled', 'draft': 'test', 'regenerate': 'regenerate'}[args.mode]
                    job = engine.enqueue(task, edition, purpose, request_id, now, reuse_cover=args.reuse_cover, include_item_ids=args.include_item_id)
                    result = engine.job_status(job)
                    result['request_id'] = request_id if args.mode != 'scheduled' else None
                else:
                    if not args.job_id:
                        raise ValueError('请指定 --job-id')
                    job = GenerationJob.query.get(args.job_id)
                    if not job or job.environment != args.environment:
                        raise ValueError('当前环境中不存在此执行任务')
                    if args.command == 'retry':
                        job = engine.control_job(job.id, 'retry', args.environment)
                    result = engine.job_status(job)
                print(json.dumps(result, ensure_ascii=False))
                return 0
            except (ValueError, GenerationError) as error:
                db.session.rollback()
                print(json.dumps({'error': getattr(error, 'code', 'invalid_command'), 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
                return 2
    if os.environ.get('CONTENT_JOBS_ENABLED') != '1' or os.environ.get('CONTENT_ENVIRONMENT') != args.environment:
        parser.error('执行需设置 CONTENT_JOBS_ENABLED=1，并使 CONTENT_ENVIRONMENT 与 --environment 一致')
    if args.environment == 'production' and os.environ.get('FLASK_CONFIG') != 'production':
        parser.error('生产任务只允许 FLASK_CONFIG=production 的进程执行')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    identity = '{}:{}:{}'.format(args.command, socket.gethostname()[:50], uuid.uuid4().hex)
    active = {'claim': None}
    def heartbeat():
        while not stop.is_set():
            try:
                with app.app_context():
                    row = GenerationHeartbeat.query.get(identity)
                    if row is None:
                        row = GenerationHeartbeat(identity=identity, role=args.command, environment=args.environment)
                        db.session.add(row)
                    row.last_seen_at = utcnow()
                    db.session.commit()
                    current = active['claim']
                    if current:
                        engine.renew(current[0], current[1])
            except Exception:
                app.logger.exception('Content worker heartbeat failed')
            stop.wait(15)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            try:
                with app.app_context():
                    if args.command == 'scheduler':
                        engine.tick(args.environment)
                    elif args.command == 'collector':
                        from app.generation.sources import collect
                        ids = set()
                        for task in GenerationTask.query.filter_by(environment=args.environment).all():
                            ids.update(json.loads(engine.task_version(task).config_json)['source_ids'])
                        if ids:
                            from app.generation.configuration import default_config
                            config = default_config()
                            config.update(source_ids=sorted(ids), max_lookback_hours=168)
                            collect(config, utcnow(), capture_only=True)
                    else:
                        current = engine.claim(args.environment)
                        active['claim'] = current
                        if current:
                            engine.execute(*current)
                        active['claim'] = None
            except Exception:
                active['claim'] = None
                app.logger.exception('Content process iteration failed')
            if args.once:
                break
            stop.wait({'scheduler': 30, 'collector': 900}.get(args.command, 5))
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == '__main__':
    sys.exit(main() or 0)
