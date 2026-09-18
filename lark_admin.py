"""CLI entry point; importing the website never starts a listener."""
import argparse
import fcntl
import json
import logging
import os
import signal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'check', 'status', 'catalog'])
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from app import create_app
    from lark_bridge.service import Bridge
    bridge = Bridge(create_app(os.environ.get('FLASK_CONFIG', 'production')))
    if args.command == 'check':
        bridge.site.admin_token()
        bridge.lark.verify()
        result = bridge.lark.run(['event', 'consume', 'im.message.receive_v1', '--as', 'bot', '--dry-run'])
        print(json.dumps({'app_id': bridge.app_id, 'admin_ready': True, 'event': result,
                          'card_callback_required': '在飞书控制台启用 card.action.trigger 回调；CLI ready 无法验证该设置',
                          'api_count': len(bridge.site.catalog())}, ensure_ascii=False))
        return
    if args.command == 'catalog':
        print(json.dumps(bridge.site.catalog(), ensure_ascii=False, indent=2))
        return
    if args.command == 'status':
        with bridge.store.connect() as c:
            print(json.dumps({'jobs': [dict(r) for r in c.execute('SELECT id,state,updated FROM jobs ORDER BY id DESC LIMIT 10')],
                              'reviews': [dict(r) for r in c.execute('SELECT kind,target_id,state FROM reviews ORDER BY created DESC LIMIT 10')],
                              'pending_replies': c.execute('SELECT COUNT(*) FROM outbox WHERE sent=0').fetchone()[0]}))
        return
    with (bridge.store.root / 'service.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('已有桥接服务运行，不能启动第二个 worker')
        def stop(*_):
            bridge.stop.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        bridge.run()


if __name__ == '__main__':
    main()
