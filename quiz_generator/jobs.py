"""Durable UI jobs with one sequential worker and lossless output paths."""

from copy import deepcopy
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from .inputs import plan_source_units, validate_relative_path
from .pipeline import OutputLock, QuizRunner, atomic_json

LOG = logging.getLogger(__name__)
ACTIVE = {'queued', 'running', 'stopping'}


class JobManager:
    def __init__(self, work_root: Path, output_root: Path, generator_factory):
        self.work_root = work_root.resolve()
        self.output_root = output_root.resolve()
        self.generator_factory = generator_factory
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.jobs = {}
        self.active_id = None
        self.stop_events = {}
        self.reserved = set()
        self.shutdown_event = threading.Event()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._load()
        self.worker = threading.Thread(target=self._dispatch, daemon=True, name='quiz-queue')
        self.worker.start()

    def _load(self):
        for metadata in self.work_root.glob('*/job.json'):
            try:
                job = json.loads(metadata.read_text(encoding='utf-8'))
                if job['id'] != metadata.parent.name or not isinstance(job['files'], list):
                    continue
                for file in job['files']:
                    validate_relative_path(file['path'])
                    validate_relative_path(file['normalized_path'])
                    if file.get('output'):
                        validate_relative_path(file['output'])
                if job['status'] in ('running', 'stopping'):
                    job['status'] = 'interrupted'
                    for file in job['files']:
                        if file['status'] in ('reading', 'generating'):
                            file['status'] = 'pending'
                    job['error'] = 'Ứng dụng đã khởi động lại. Các quiz hoàn thành vẫn có thể tải xuống.'
                    atomic_json(metadata, job)
                self.jobs[job['id']] = job
                self.stop_events[job['id']] = threading.Event()
            except (OSError, ValueError, KeyError, TypeError):
                LOG.warning('Không đọc được metadata job %s.', metadata.parent.name)

    def reserve(self) -> str:
        with self.lock:
            job_id = uuid.uuid4().hex
            self.reserved.add(job_id)
            return job_id

    def release(self, job_id: str):
        with self.lock:
            self.reserved.discard(job_id)

    def start(self, job_id: str, kind: str, mapping: dict, sizes: dict) -> dict:
        with self.lock:
            now = time.time()
            plans = plan_source_units(
                tuple(mapping), mapping, group_leaf_folders=kind == 'folder',
                root_name=next(iter(mapping.values())).split('/')[0],
            )
            job = {
                'id': job_id, 'kind': kind, 'name': next(iter(mapping.values())).split('/')[0],
                'status': 'queued', 'created_at': now, 'updated_at': now, 'error': '',
                'files': [{'id': str(index), 'path': plan.path, 'normalized_path': plan.key,
                           'sources': list(plan.original_paths),
                           'normalized_sources': list(plan.relative_paths),
                           'target_output': plan.output,
                           'normalized_output': plan.normalized_output,
                           'size': sum(sizes[path] for path in plan.relative_paths),
                           'status': 'pending', 'error': '', 'output': None}
                          for index, plan in enumerate(plans)],
            }
            self.jobs[job_id] = job
            self.stop_events[job_id] = threading.Event()
            self.reserved.discard(job_id)
            self._save(job)
            self.condition.notify_all()
            return self.snapshot(job_id)

    def _save(self, job):
        job['updated_at'] = time.time()
        atomic_json(self.work_root / job['id'] / 'job.json', job)

    def _progress(self, job_id, event):
        with self.lock:
            job = self.jobs[job_id]
            for file in job['files']:
                if file['normalized_path'] == event['path']:
                    file['status'] = event['status']
                    for key in ('error', 'output', 'pages'):
                        if key in event:
                            file[key] = event[key]
                    break
            self._save(job)

    def _next_queued_locked(self):
        queued = [job for job in self.jobs.values() if job['status'] == 'queued']
        return min(queued, key=lambda job: (job['created_at'], job['id'])) if queued else None

    def _dispatch(self):
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: self.shutdown_event.is_set() or self._next_queued_locked() is not None
                )
                if self.shutdown_event.is_set():
                    return
                job = self._next_queued_locked()
                job_id = job['id']
                self.active_id = job_id
                job['status'] = 'running'
                self._save(job)
            self._run_job(job_id)
            with self.condition:
                self.active_id = None
                self.condition.notify_all()

    def _run_job(self, job_id):
        generator = None
        try:
            generator = self.generator_factory()
            with self.lock:
                job = self.jobs[job_id]
                job['model'] = generator.model
                job['status'] = 'stopping' if self.stop_events[job_id].is_set() else 'running'
                self._save(job)
                mapping_file = self.work_root / job_id / 'path-mapping.json'
                if mapping_file.is_file():
                    mapping = json.loads(mapping_file.read_text(encoding='utf-8'))
                else:
                    mapping = {file['normalized_path']: file['path'] for file in job['files']}
            output = self.output_root / job_id
            with OutputLock(output):
                runner = QuizRunner(self.work_root / job_id / 'input', output, generator,
                                    path_mapping=mapping, stop_event=self.stop_events[job_id],
                                    group_leaf_folders=job.get('kind') == 'folder',
                                    on_progress=lambda event: self._progress(job_id, event))
                summary = runner.run_once()
            with self.lock:
                job['status'] = ('stopped' if self.stop_events[job_id].is_set() else
                                 'completed_with_errors' if summary.failed else 'completed')
                self._save(job)
        except Exception as error:
            message = str(error)
            if generator is not None and generator.api_key:
                message = message.replace(generator.api_key, '[redacted]')
            LOG.error('Job %s: %s', job_id, message)
            with self.lock:
                job = self.jobs[job_id]
                job['status'], job['error'] = 'failed', message
                for file in job['files']:
                    if file['status'] in ('reading', 'generating'):
                        file['status'], file['error'] = 'failed', message
                self._save(job)
        finally:
            if generator is not None:
                generator.close()

    def snapshot(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            job = deepcopy(self.jobs[job_id])
            counts = {'total': len(job['files']), 'done': 0, 'failed': 0, 'pending': 0, 'active': 0}
            for file in job['files']:
                if file['status'] in ('done', 'skipped'):
                    counts['done'] += 1
                elif file['status'] == 'failed':
                    counts['failed'] += 1
                elif file['status'] == 'pending':
                    counts['pending'] += 1
                else:
                    counts['active'] += 1
            job['counts'] = counts
            queued = sorted(
                (candidate for candidate in self.jobs.values() if candidate['status'] == 'queued'),
                key=lambda candidate: (candidate['created_at'], candidate['id']),
            )
            job['queue_position'] = (
                next((index for index, candidate in enumerate(queued, 1)
                      if candidate['id'] == job_id), None)
            )
            return job

    def list_jobs(self):
        with self.lock:
            return [self.snapshot(job_id) for job_id in
                    sorted(self.jobs, key=lambda key: self.jobs[key]['created_at'], reverse=True)]

    def stop(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            status = self.jobs[job_id]['status']
            if status == 'queued':
                self.stop_events[job_id].set()
                self.jobs[job_id]['status'] = 'stopped'
                self._save(self.jobs[job_id])
                self.condition.notify_all()
            elif status in ('running', 'stopping'):
                self.stop_events[job_id].set()
                self.jobs[job_id]['status'] = 'stopping'
                self._save(self.jobs[job_id])
            return self.snapshot(job_id)

    def result_path(self, job_id: str, file_id: str) -> tuple[Path, str]:
        job = self.snapshot(job_id)
        file = next((file for file in job['files'] if file['id'] == file_id), None)
        if not file or file['status'] not in ('done', 'skipped') or not file.get('output'):
            raise KeyError(file_id)
        validate_relative_path(file['output'])
        root = (self.output_root / job_id).resolve()
        path = (root / file['output']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError(file_id)
        return path, file['output']

    def shutdown(self):
        with self.condition:
            self.shutdown_event.set()
            if self.active_id is not None:
                self.stop_events[self.active_id].set()
                if self.jobs[self.active_id]['status'] == 'running':
                    self.jobs[self.active_id]['status'] = 'stopping'
                    self._save(self.jobs[self.active_id])
            self.condition.notify_all()
        if self.worker.is_alive():
            self.worker.join(timeout=1)
