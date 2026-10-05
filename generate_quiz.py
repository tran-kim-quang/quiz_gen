#!/usr/bin/env python3
"""Generate quizzes in batch, or continuously watch a document tree."""

import argparse
import logging
from pathlib import Path
import sys
import time

from quiz_generator.config import create_generator
from quiz_generator.documents import discover_documents
from quiz_generator.inputs import plan_source_units
from quiz_generator.pipeline import OutputLock, QuizRunner

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('quiz_generator')


def parser():
    result = argparse.ArgumentParser(description='Sinh một JSON quiz cho mỗi folder lá theo cấu trúc bulk upload.')
    result.add_argument('--documents', type=Path, default=ROOT / 'documents', help='Thư mục nguồn (quét đệ quy).')
    result.add_argument('--output', type=Path, default=ROOT / 'outputs', help='Thư mục lưu JSON.')
    result.add_argument('--env-file', type=Path, default=ROOT / '.env', help='File chứa API key và model.')
    result.add_argument('--watch', action='store_true', help='Chạy liên tục, theo dõi file mới/thay đổi.')
    result.add_argument('--interval', type=float, default=10, help='Khoảng quét trong watch, giây (0 < n ≤ 60).')
    result.add_argument('--failure-cooldown', type=float, default=300, help='Thời gian chờ trước khi thử lại file lỗi, giây.')
    result.add_argument('--force', action='store_true', help='Sinh lại các file đã hoàn thành trong lượt đầu.')
    result.add_argument('--limit', type=int, help='Dừng sau khi thử xử lý n folder lá, tính cả folder lỗi.')
    result.add_argument('--list', action='store_true', help='Liệt kê nguồn, không gọi API.')
    result.add_argument('--text-only', action='store_true', help='Chỉ gửi text; không dùng cho tài liệu scan/ảnh.')
    result.add_argument('--max-pages', type=int, default=80, help='Giới hạn trang/slide mỗi tài liệu.')
    result.add_argument('--max-chars', type=int, default=200_000, help='Giới hạn ký tự text mỗi tài liệu.')
    result.add_argument('--max-image-mb', type=float, default=20, help='Giới hạn tổng bytes ảnh base64 theo MB mỗi tài liệu.')
    result.add_argument('--image-edge', type=int, default=1600, help='Kích thước cạnh dài ảnh gửi model, pixel.')
    return result


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if not 0 < args.interval <= 60:
        argument_parser.error('--interval phải lớn hơn 0 và không quá 60.')
    if args.failure_cooldown < 0 or (args.limit is not None and args.limit < 1):
        argument_parser.error('--failure-cooldown ≥ 0 và --limit ≥ 1.')
    if min(args.max_pages, args.max_chars, args.max_image_mb, args.image_edge) <= 0:
        argument_parser.error('Các giới hạn tài liệu phải lớn hơn 0.')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    generator = None
    try:
        sources = discover_documents(args.documents)
        relative_sources = tuple(path.relative_to(args.documents).as_posix() for path in sources)
        plans = plan_source_units(relative_sources, root_name=args.documents.name)
        if args.list:
            for plan in plans:
                print(f'{plan.path} ({len(plan.relative_paths)} tài liệu) -> output lấy từ title quiz')
                for source in plan.relative_paths:
                    print(f'  - {source}')
            print(f'Tổng: {len(plans)} folder lá, {len(sources)} tài liệu.')
            return 0
        generator = create_generator(args.env_file)
        LOG.info('Model: %s; tìm thấy %d folder lá, %d tài liệu.',
                 generator.model, len(plans), len(sources))
        with OutputLock(args.output):
            runner = QuizRunner(args.documents, args.output, generator,
                                failure_cooldown=args.failure_cooldown,
                                reader_options={
                                    'text_only': args.text_only, 'max_pages': args.max_pages,
                                    'max_chars': args.max_chars, 'image_edge': args.image_edge,
                                    'max_image_bytes': int(args.max_image_mb * 1_000_000),
                                })
            attempted = 0
            force = args.force
            while True:
                summary = runner.run_once(force=force, limit=args.limit - attempted if args.limit else None)
                force = False
                attempted += summary.attempted
                LOG.info('Lượt xử lý: tạo %d, bỏ qua %d, lỗi %d, chờ retry %d.',
                         summary.generated, summary.skipped, summary.failed, summary.deferred)
                if not args.watch or (args.limit is not None and attempted >= args.limit):
                    return 1 if summary.failed or summary.deferred or runner.has_unresolved_failures() else 0
                if summary.attempted:
                    LOG.info('Đang theo dõi tài liệu mới; Ctrl+C để dừng.')
                time.sleep(args.interval)
    except KeyboardInterrupt:
        LOG.info('Đã dừng. Kết quả hoàn thành đã được lưu; chạy lại để tiếp tục.')
        return 130
    except Exception as error:
        LOG.error('%s', error)
        return 2
    finally:
        if generator is not None:
            generator.close()


if __name__ == '__main__':
    raise SystemExit(main())
