"""Launch the API and exactly one collector worker. Ctrl+C stops both."""
import argparse
import socket
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

import config
from web import db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--web-only', action='store_true', help='View stored data without collecting new images')
    parser.add_argument('--watch-only', action='store_true', help='Use the existing collector; only analyze its new images')
    args = parser.parse_args()
    if args.web_only and args.watch_only:
        parser.error('Choose either --web-only or --watch-only')
    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError:
            raise SystemExit(f'Port {args.port} is already in use. Close the old dashboard or choose --port.')
    db.init_db()
    if not args.web_only and db.state()['worker_online']:
        raise SystemExit('A collector worker is already active. Use --web-only to view its data.')
    with db.connect() as conn:
        empty = conn.execute('SELECT COUNT(*) FROM frames').fetchone()[0] == 0
    if empty and config.OCCUPANCY_CSV.exists():
        from web.import_csv import import_csv
        print('Importing existing CSV:', import_csv(config.OCCUPANCY_CSV), flush=True)
    children, logs = [], []
    try:
        for name, command in [('web', [sys.executable, '-m', 'uvicorn', 'web.app:app', '--host', args.host,
                                      '--port', str(args.port), '--workers', '1'])] + ([] if args.web_only else
                                   [('worker', [sys.executable, 'run_monitor.py'] + (['--watch-only'] if args.watch_only else []))]):
            log = open(config.RESULT_DIR / f'dashboard-{name}.log', 'a', encoding='utf-8')
            logs.append(log)
            children.append(subprocess.Popen(command, cwd=config.ROOT, stdout=log, stderr=log,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform=='win32' else 0))
        url = f'http://127.0.0.1:{args.port}'
        for _ in range(50):
            if any(p.poll() is not None for p in children):
                raise RuntimeError('A process exited. See results/dashboard-web.log and dashboard-worker.log')
            try:
                with urllib.request.urlopen(url+'/api/health', timeout=1):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError('Web server did not start. See results/dashboard-web.log')
        print(f'Dashboard: {url}\nPress Ctrl+C to stop. Logs: {config.RESULT_DIR}', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in children):
            time.sleep(1)
        raise RuntimeError('A process stopped unexpectedly. Check dashboard logs.')
    except KeyboardInterrupt:
        print('\nStopping dashboard...')
    finally:
        for child in children:
            if child.poll() is None:
                if sys.platform=='win32':
                    try:
                        child.send_signal(signal.CTRL_BREAK_EVENT)
                    except OSError:
                        child.terminate()
                else:
                    child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    def request_stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, request_stop)
    main()
