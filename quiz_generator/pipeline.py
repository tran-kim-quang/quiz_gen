"""Sequential queue with durable state and mirrored, atomic JSON outputs."""

from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time

from .documents import Document, discover_documents, read_document
from .llm import FatalAPIError, validate_quiz
from .inputs import plan_source_units, validate_relative_path

LOG = logging.getLogger(__name__)


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class OutputLock:
    """The OS releases this lock even after interruption or process crashes."""

    def __init__(self, output_root: Path):
        self.path = output_root / '.quiz-worker.lock'
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise RuntimeError('Đã có worker/tiến trình khác dùng thư mục output này.') from None
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()) + '\n')
        self.handle.flush()
        return self

    def __exit__(self, *args):
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


@dataclass
class RunSummary:
    generated: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0

    @property
    def attempted(self):
        return self.generated + self.failed


class QuizRunner:
    def __init__(self, documents_root: Path, output_root: Path, generator, *,
                 reader_options=None, failure_cooldown: float = 300,
                 path_mapping=None, on_progress=None, stop_event=None,
                 group_leaf_folders: bool = True):
        self.documents_root = documents_root.resolve()
        self.output_root = output_root.resolve()
        self.generator = generator
        self.reader_options = reader_options or {}
        self.failure_cooldown = failure_cooldown
        self.path_mapping = dict(path_mapping) if path_mapping is not None else None
        if self.path_mapping is not None:
            for normalized, original in self.path_mapping.items():
                validate_relative_path(normalized)
                validate_relative_path(original)
        self.group_leaf_folders = group_leaf_folders
        self.on_progress = on_progress
        self.stop_event = stop_event
        self.state_path = self.output_root / '.quiz-state.json'
        self.state = {'version': 1, 'files': {}}
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding='utf-8'))
                if not isinstance(loaded, dict) or loaded.get('version') != 1 or not isinstance(loaded.get('files'), dict):
                    raise ValueError('State không hợp lệ')
                if any(not isinstance(entry, dict) for entry in loaded['files'].values()):
                    raise ValueError('State không hợp lệ')
                self.state = loaded
            except (ValueError, OSError):
                LOG.warning('State không đọc được; sẽ kiểm tra và xử lý lại các tài liệu.')

    def _plans(self):
        paths = discover_documents(self.documents_root)
        relatives = tuple(path.relative_to(self.documents_root).as_posix() for path in paths)
        return plan_source_units(
            relatives, self.path_mapping, group_leaf_folders=self.group_leaf_folders,
            root_name=self.documents_root.name,
        )

    def _fingerprint(self, plan) -> str:
        context = {
            'source_root': str(self.documents_root), 'model': self.generator.model,
            'prompt': self.generator.system_prompt, 'base_url': self.generator.base_url,
            'temperature': self.generator.temperature, 'max_tokens': self.generator.max_tokens,
            'reader': self.reader_options, 'pipeline_version': 2,
            'group_leaf_folders': self.group_leaf_folders,
            'sources': plan.original_paths,
        }
        digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode('utf-8'))
        for relative in plan.relative_paths:
            digest.update(relative.encode('utf-8'))
            with (self.documents_root / relative).open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
        return digest.hexdigest()

    def _done(self, output: Path, entry: dict, fingerprint: str, relative: str) -> bool:
        if entry.get('status') != 'done' or entry.get('fingerprint') != fingerprint or not output.is_file():
            return False
        try:
            validate_quiz(output.read_text(encoding='utf-8'), relative)
            return True
        except (OSError, ValueError):
            return False

    def has_unresolved_failures(self) -> bool:
        return any(entry.get('status') == 'failed' and any(
            (self.documents_root / relative).is_file()
            for relative in entry.get('sources', (key,))
        ) for key, entry in self.state['files'].items())

    def _emit(self, relative: str, status: str, **details):
        if self.on_progress is not None:
            self.on_progress({'path': relative, 'status': status, **details})

    def _read_group(self, plan) -> Document:
        texts, images = [], []
        pages = 0
        for index, relative in enumerate(plan.relative_paths, 1):
            document = read_document(self.documents_root / relative, **self.reader_options)
            pages += document.page_count
            images.extend(document.images)
            texts.append(f'--- TÀI LIỆU {index}: {relative} ---\n{document.text}')
        max_pages = self.reader_options.get('max_pages', 80)
        max_chars = self.reader_options.get('max_chars', 200_000)
        max_image_bytes = self.reader_options.get('max_image_bytes', 20_000_000)
        text = '\n\n'.join(texts)
        if pages > max_pages:
            raise ValueError(f'Nhóm tài liệu có {pages} trang/slide, vượt giới hạn {max_pages}.')
        if len(text) > max_chars:
            raise ValueError(f'Nhóm tài liệu có {len(text)} ký tự, vượt giới hạn {max_chars}.')
        image_bytes = sum(len(value) for value in images)
        if image_bytes > max_image_bytes:
            raise ValueError(f'Ảnh của nhóm tài liệu vượt giới hạn {max_image_bytes} bytes.')
        return Document(text=text, images=images, page_count=pages)

    def run_once(self, *, force: bool = False, limit: int | None = None) -> RunSummary:
        summary = RunSummary()
        for plan in self._plans():
            if self.stop_event is not None and self.stop_event.is_set():
                break
            if limit is not None and summary.attempted >= limit:
                break
            relative = plan.key
            output = self.output_root / plan.output
            entry = self.state['files'].get(relative, {})
            fingerprint = None
            try:
                fingerprint = self._fingerprint(plan)
                if not force and self._done(output, entry, fingerprint, plan.validation_path):
                    summary.skipped += 1
                    self._emit(relative, 'skipped', output=plan.output,
                               sources=list(plan.original_paths))
                    continue
                if (not force and entry.get('status') == 'failed'
                        and entry.get('fingerprint') == fingerprint
                        and entry.get('retry_at', 0) > time.time()):
                    summary.deferred += 1
                    continue
                LOG.info('Đang tạo quiz từ folder lá: %s (%d tài liệu).', relative, len(plan.relative_paths))
                self._emit(relative, 'reading', sources=list(plan.original_paths))
                document = self._read_group(plan)
                LOG.info('Nguồn: %d tài liệu, %d trang/slide, %d ảnh, %d ký tự text.',
                         len(plan.relative_paths), document.page_count, len(document.images), len(document.text))
                self._emit(relative, 'generating', pages=document.page_count)
                quiz = self.generator.generate(document, plan.validation_path,
                                                source_paths=plan.relative_paths)
                if fingerprint != self._fingerprint(plan):
                    raise ValueError('Tài liệu thay đổi trong khi sinh quiz; sẽ xử lý lại phiên bản mới.')
                # Validate again at the persistence boundary.
                validate_quiz(json.dumps(quiz, ensure_ascii=False), plan.validation_path)
                atomic_json(output, quiz)
                self.state['files'][relative] = {
                    'fingerprint': fingerprint, 'status': 'done',
                    'sources': list(plan.relative_paths),
                    'output': str(output.relative_to(self.output_root)), 'updated_at': time.time(),
                }
                summary.generated += 1
                LOG.info('Đã lưu: %s', output)
                event = {'status': 'done', 'output': plan.output,
                         'sources': list(plan.original_paths)}
            except FatalAPIError:
                raise
            except Exception as error:
                # Never persist provider response bodies or key material.
                LOG.error('Không xử lý được %s: %s', relative, error)
                self.state['files'][relative] = {
                    'fingerprint': fingerprint, 'status': 'failed',
                    'sources': list(plan.relative_paths),
                    'error_type': type(error).__name__, 'retry_at': time.time() + self.failure_cooldown,
                }
                summary.failed += 1
                event = {'status': 'failed', 'error': str(error)}
            atomic_json(self.state_path, self.state)
            self._emit(relative, **event)
        return summary
