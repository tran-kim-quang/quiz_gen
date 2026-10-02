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

from .documents import discover_documents, read_document
from .llm import FatalAPIError, validate_quiz
from .inputs import validate_relative_path, validate_output_paths
from .planning import PlannedDocument, SourceFile

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
                 document_provider=None):
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
            validate_output_paths(list(self.path_mapping.values()))
        self.on_progress = on_progress
        self.document_provider = document_provider or self._default_documents
        self._last_state_keys = set()
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

    def _default_documents(self):
        documents = []
        for path in discover_documents(self.documents_root):
            relative = path.relative_to(self.documents_root).as_posix()
            original = (
                self.path_mapping[relative]
                if self.path_mapping is not None
                else relative
            )
            member = SourceFile(path, relative, original)
            documents.append(PlannedDocument(
                source_path=path,
                state_key=relative,
                prompt_path=relative,
                output_path=original + '.json',
                kind='standalone',
                action='standalone',
                members=(member,),
            ))
        return documents

    def _planned_documents(self):
        documents = tuple(self.document_provider())
        state_keys = set()
        output_keys = set()
        for document in documents:
            if not isinstance(document, PlannedDocument):
                raise TypeError('Document provider phải trả về PlannedDocument.')
            validate_relative_path(document.state_key)
            validate_relative_path(document.prompt_path)
            validate_relative_path(document.output_path)
            if document.source_path is None:
                raise ValueError('Tài liệu chưa có đường dẫn vật lý.')
            if document.state_key in state_keys:
                raise ValueError(f'Trùng state key: {document.state_key}')
            output_key = document.output_path.casefold()
            if output_key in output_keys:
                raise ValueError(f'Trùng output quiz: {document.output_path}')
            state_keys.add(document.state_key)
            output_keys.add(output_key)
            self._output_path(document.output_path)
        self._last_state_keys = state_keys
        return documents

    def _output_path(self, relative: str) -> Path:
        target = (self.output_root / relative).resolve()
        if not target.is_relative_to(self.output_root):
            raise ValueError('Đường dẫn quiz output nằm ngoài thư mục được cấu hình.')
        return target

    def _fingerprint(self, document: PlannedDocument) -> str:
        context = {
            'source_root': str(self.documents_root), 'model': self.generator.model,
            'prompt': self.generator.system_prompt, 'base_url': self.generator.base_url,
            'temperature': self.generator.temperature, 'max_tokens': self.generator.max_tokens,
            'reader': self.reader_options, 'pipeline_version': 2,
            'prompt_path': document.prompt_path,
            'output_path': document.output_path,
        }
        path = document.source_path
        digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode('utf-8'))
        with path.open('rb') as handle:
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
        return any(
            self.state['files'].get(key, {}).get('status') == 'failed'
            for key in self._last_state_keys
        )

    def _emit(self, relative: str, status: str, **details):
        if self.on_progress is not None:
            self.on_progress({'path': relative, 'status': status, **details})

    def run_once(self, *, force: bool = False, limit: int | None = None) -> RunSummary:
        summary = RunSummary()
        for planned in self._planned_documents():
            if self.stop_event is not None and self.stop_event.is_set():
                break
            if limit is not None and summary.attempted >= limit:
                break
            path = planned.source_path
            relative = planned.state_key
            output = self._output_path(planned.output_path)
            entry = self.state['files'].get(planned.state_key, {})
            fingerprint = None
            try:
                fingerprint = self._fingerprint(planned)
                if not force and self._done(output, entry, fingerprint, relative):
                    summary.skipped += 1
                    self._emit(relative, 'skipped', output=planned.output_path)
                    continue
                if (not force and entry.get('status') == 'failed'
                        and entry.get('fingerprint') == fingerprint
                        and entry.get('retry_at', 0) > time.time()):
                    summary.deferred += 1
                    continue
                LOG.info('Đang tạo quiz: %s', relative)
                self._emit(relative, 'reading')
                document = read_document(path, **self.reader_options)
                LOG.info('Nguồn: %d trang/slide, %d ảnh, %d ký tự text.',
                         document.page_count, len(document.images), len(document.text))
                self._emit(relative, 'generating', pages=document.page_count)
                quiz = self.generator.generate(document, planned.prompt_path)
                if fingerprint != self._fingerprint(planned):
                    raise ValueError('Tài liệu thay đổi trong khi sinh quiz; sẽ xử lý lại phiên bản mới.')
                # Validate again at the persistence boundary.
                validate_quiz(json.dumps(quiz, ensure_ascii=False), planned.prompt_path)
                atomic_json(output, quiz)
                self.state['files'][relative] = {
                    'fingerprint': fingerprint, 'status': 'done',
                    'output': planned.output_path, 'updated_at': time.time(),
                }
                summary.generated += 1
                LOG.info('Đã lưu: %s', output)
                event = {'status': 'done', 'output': planned.output_path}
            except FatalAPIError:
                raise
            except Exception as error:
                # Never persist provider response bodies or key material.
                LOG.error('Không xử lý được %s: %s', relative, error)
                self.state['files'][relative] = {
                    'fingerprint': fingerprint, 'status': 'failed',
                    'error_type': type(error).__name__, 'retry_at': time.time() + self.failure_cooldown,
                }
                summary.failed += 1
                event = {'status': 'failed', 'error': str(error)}
            atomic_json(self.state_path, self.state)
            self._emit(relative, **event)
        return summary
