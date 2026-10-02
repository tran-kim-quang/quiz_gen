#!/usr/bin/env python3
"""Start the local Quiz Studio interface."""

import argparse
import logging
from pathlib import Path

import uvicorn

from quiz_generator.config import ROOT
from quiz_generator.web import create_app


def main():
    parser = argparse.ArgumentParser(description='Quiz Studio — giao diện sinh quiz từ File/Folder.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    uvicorn.run(create_app(env_file=args.env_file), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
