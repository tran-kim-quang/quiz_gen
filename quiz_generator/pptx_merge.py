"""Invoke the native Node PPTX merge worker through a strict JSON protocol."""

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER = ROOT / 'tools' / 'pptx-merge-worker.mjs'


@dataclass(frozen=True)
class MergeResult:
    part1_slides: int
    part2_slides: int
    output_slides: int


class SourceMergeError(RuntimeError):
    """The input presentations cannot be merged safely."""


class MergeInfrastructureError(RuntimeError):
    """The merge runtime or its protocol failed."""


class NodePptxMerger:
    def __init__(
        self,
        node_binary: str = 'node',
        worker_path: Path = DEFAULT_WORKER,
        timeout: float = 300,
    ):
        self.node_binary = node_binary
        self.worker_path = Path(worker_path)
        self.timeout = timeout

    def merge(self, part1: Path, part2: Path, output: Path) -> MergeResult:
        arguments = [
            self.node_binary,
            str(self.worker_path),
            '--part1',
            str(part1),
            '--part2',
            str(part2),
            '--output',
            str(output),
        ]
        try:
            process = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise MergeInfrastructureError(
                'Worker ghép PPTX đã quá thời gian cho phép.'
            ) from error
        except OSError as error:
            raise MergeInfrastructureError(
                'Không thể khởi động Node.js worker ghép PPTX.'
            ) from error

        payload = self._parse_payload(process.stdout)
        if (
            process.returncode != 0
            and payload.get('ok') is False
            and payload.get('kind') == 'source'
            and isinstance(payload.get('error'), str)
            and payload['error']
        ):
            raise SourceMergeError(payload['error'])
        if process.returncode != 0:
            raise MergeInfrastructureError('Node.js worker ghép PPTX kết thúc bất thường.')

        expected_keys = {
            'ok', 'part1Slides', 'part2Slides', 'outputSlides',
        }
        if payload.keys() != expected_keys or payload.get('ok') is not True:
            raise MergeInfrastructureError('Node.js worker trả về protocol không hợp lệ.')
        counts = (
            payload.get('part1Slides'),
            payload.get('part2Slides'),
            payload.get('outputSlides'),
        )
        if (
            any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in counts)
            or counts[0] + counts[1] != counts[2]
        ):
            raise MergeInfrastructureError('Node.js worker trả về số slide không nhất quán.')
        if not Path(output).is_file():
            raise MergeInfrastructureError('Node.js worker không tạo file PPTX đầu ra.')
        return MergeResult(*counts)

    @staticmethod
    def _parse_payload(stdout: str) -> dict:
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise MergeInfrastructureError(
                'Node.js worker trả về protocol không hợp lệ.'
            ) from error
        if not isinstance(payload, dict):
            raise MergeInfrastructureError('Node.js worker trả về protocol không hợp lệ.')
        return payload
class PptxMaterializer:
    def __init__(self, merger: NodePptxMerger):
        self.merger = merger

    def materialize(self, plan, on_event=None):
        from dataclasses import replace
        import fcntl
        import hashlib
        import os
        import tempfile

        from .planning import DocumentPlan, PlanIssue, standalone_document

        if plan.merged_root is None:
            raise ValueError('Kế hoạch thiếu thư mục lưu PPTX đã ghép.')
        merged_root = Path(plan.merged_root).resolve()
        merged_root.mkdir(parents=True, exist_ok=True)
        lock_root = merged_root / '.locks'
        lock_root.mkdir(parents=True, exist_ok=True)
        final_documents = []
        final_issues = list(plan.issues)

        def emit(event, document, warning='', result=None):
            if on_event is not None:
                payload = {
                    'event': event,
                    'state_key': document.state_key,
                    'warning': warning,
                    'members': [
                        member.original_path for member in document.members
                    ],
                    'destination': str(document.source_path),
                }
                if result is not None:
                    payload['slide_counts'] = {
                        'part1': result.part1_slides,
                        'part2': result.part2_slides,
                        'output': result.output_slides,
                    }
                on_event(payload)

        for document in plan.documents:
            if document.kind != 'merged':
                final_documents.append(document)
                continue
            if document.source_path is None:
                raise ValueError('Tài liệu ghép không có đường dẫn đích.')

            destination = document.source_path.resolve()
            if not destination.is_relative_to(merged_root):
                raise ValueError('Đường dẫn output nằm ngoài thư mục PPTX đã ghép.')
            digest = hashlib.sha256(str(destination).encode('utf-8')).hexdigest()
            lock_path = lock_root / f'{digest}.lock'
            with lock_path.open('a+b') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                if destination.is_file():
                    reused = replace(document, action='reuse')
                    final_documents.append(reused)
                    emit('reused', reused, reused.warning)
                    continue

                if len(document.members) != 2 or any(
                    member.path is None for member in document.members
                ):
                    raise ValueError('Tài liệu ghép phải có đúng hai file nguồn.')
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f'.{destination.stem}.tmp-',
                    suffix='.pptx',
                    dir=destination.parent,
                )
                os.close(descriptor)
                temporary = Path(temporary_name)
                emit('merging', document, document.warning)
                try:
                    merge_result = self.merger.merge(
                        document.members[0].path,
                        document.members[1].path,
                        temporary,
                    )
                    if not temporary.is_file():
                        raise MergeInfrastructureError(
                            'Worker ghép PPTX không tạo file tạm đầu ra.'
                        )
                    os.replace(temporary, destination)
                except SourceMergeError as error:
                    warning = f'Không thể ghép tự động: {error}; xử lý từng part độc lập.'
                    fallbacks = tuple(
                        standalone_document(member, warning=warning)
                        for member in document.members
                    )
                    final_documents.extend(fallbacks)
                    final_issues.append(PlanIssue(
                        code='merge_fallback',
                        message=warning,
                        members=tuple(
                            member.original_path for member in document.members
                        ),
                    ))
                    emit('merge_fallback', document, warning)
                finally:
                    temporary.unlink(missing_ok=True)

                if destination.is_file():
                    final_documents.append(document)
                    emit('merged', document, document.warning, merge_result)

        return DocumentPlan(
            documents=tuple(final_documents),
            issues=tuple(final_issues),
            merged_root=merged_root,
        )
