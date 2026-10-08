#!/usr/bin/env python3
import argparse
import json
import os
import sys
from store import database, enqueue, initialize, load_config


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/hoag-research/config.json')
    parser.add_argument('command', choices=['web', 'worker', 'init', 'scan', 'match', 'doctor'])
    args = parser.parse_args()
    config = load_config(args.config)
    initialize(config)
    if args.command == 'web':
        from waitress import serve
        from webapp import create_app
        serve(create_app(config), host='127.0.0.1', port=8080, threads=4, max_request_body_size=12 * 1024 * 1024)
    elif args.command == 'worker':
        from worker import run
        run(config)
    elif args.command in ('scan', 'match'):
        print(json.dumps({'job': enqueue(config, args.command, actor='console')}))
    elif args.command == 'doctor':
        import engine
        for root in config['source_roots']:
            engine.checked_root(config, root)
        engine.checked_root(config, config['output_root'], output=True)
        engine.analyzer()
        print('Sources, output, database and local NLP model are available.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error) if type(error) is ValueError else type(error).__name__ + ': command failed', file=sys.stderr)
        raise SystemExit(1)
