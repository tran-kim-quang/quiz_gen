"""Local web API for lossless folder uploads and quiz downloads."""

from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .config import ROOT, create_generator, public_config
from .documents import SUPPORTED_SUFFIXES
from .inputs import build_path_mapping, plan_source_units, validate_relative_path
from .jobs import JobManager
from .pipeline import OutputLock, atomic_json

def preview(kind, paths):
    if kind not in ('file', 'folder') or not isinstance(paths, list) or not paths:
        raise ValueError('Chọn một file hoặc folder có tài liệu PDF/PPTX.')
    if any(not isinstance(path, str) for path in paths):
        raise ValueError('Danh sách đường dẫn không hợp lệ.')
    parts = [validate_relative_path(path) for path in paths]
    if kind == 'file' and (len(paths) != 1 or len(parts[0]) != 1):
        raise ValueError('Chế độ File cần đúng một file, không có đường dẫn thư mục.')
    if kind == 'folder' and (any(len(p) < 2 for p in parts) or len({p[0] for p in parts}) != 1):
        raise ValueError('Chế độ Folder cần các file trong cùng một thư mục gốc.')
    if any(Path(path).suffix.lower() not in SUPPORTED_SUFFIXES for path in paths):
        raise ValueError('Chỉ nhận tài liệu PDF và PPTX.')
    mapping = build_path_mapping(paths)
    plans = plan_source_units(tuple(mapping), mapping, group_leaf_folders=kind == 'folder',
                              root_name=parts[0][0])
    return {'name': parts[0][0], 'kind': kind,
            'files': [{'id': str(index), 'path': original, 'normalized_path': normalized}
                      for index, (normalized, original) in enumerate(mapping.items())],
            'groups': [{'id': str(index), 'path': plan.path,
                        'normalized_path': plan.key, 'output': plan.output,
                        'normalized_output': plan.normalized_output,
                        'sources': list(plan.original_paths)}
                       for index, plan in enumerate(plans)]}


def _stage(manager, job_id, uploads, mapping):
    input_root = manager.work_root / job_id / 'input'
    sizes = {}
    for upload, normalized in zip(uploads, mapping):
        path = input_root / normalized
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open('wb') as handle:
            for chunk in iter(lambda: upload.file.read(1024 * 1024), b''):
                count += len(chunk)
                handle.write(chunk)
        sizes[normalized] = count
    atomic_json(manager.work_root / job_id / 'path-mapping.json', mapping)
    return sizes


def create_app(*, work_root: Path = ROOT / '.quiz-ui' / 'jobs',
               output_root: Path = ROOT / 'outputs' / 'ui', generator_factory=None,
               env_file: Path = ROOT / '.env'):
    @asynccontextmanager
    async def lifespan(app):
        with OutputLock(work_root):
            manager = JobManager(work_root, output_root, generator_factory or (lambda: create_generator(env_file)))
            app.state.manager = manager
            try:
                yield
            finally:
                manager.shutdown()

    app = FastAPI(title='Quiz Studio', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        # Do not let another website trigger paid generation on this local app.
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return Response('Origin không hợp lệ.', status_code=403)
        return await call_next(request)

    @app.get('/api/config')
    def config():
        return {**public_config(env_file), 'max_files': None, 'max_upload_bytes': None}

    @app.post('/api/preview')
    async def get_preview(request: Request):
        try:
            body = await request.json()
            return preview(body.get('kind'), body.get('paths'))
        except (ValueError, AttributeError) as error:
            raise HTTPException(400, str(error)) from None

    @app.post('/api/jobs', status_code=202)
    async def upload_job(request: Request):
        manager = request.app.state.manager
        async with request.form(max_files=sys.maxsize, max_fields=10, max_part_size=sys.maxsize) as form:
            try:
                paths = json.loads(form.get('paths', '[]'))
                info = preview(form.get('kind'), paths)
            except (ValueError, TypeError) as error:
                raise HTTPException(400, str(error)) from None
            uploads = form.getlist('files')
            if len(uploads) != len(paths) or any(not isinstance(file, UploadFile) for file in uploads):
                raise HTTPException(400, 'Số file upload không khớp danh sách đường dẫn.')
            mapping = {file['normalized_path']: file['path'] for file in info['files']}
            job_id = manager.reserve()
            try:
                sizes = await run_in_threadpool(_stage, manager, job_id, uploads, mapping)
                return manager.start(job_id, info['kind'], mapping, sizes)
            except BaseException:
                shutil.rmtree(manager.work_root / job_id, ignore_errors=True)
                manager.release(job_id)
                raise

    @app.get('/api/jobs')
    def list_jobs(request: Request):
        return request.app.state.manager.list_jobs()

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str, request: Request):
        try:
            return request.app.state.manager.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, 'Không tìm thấy lượt xử lý.') from None

    @app.post('/api/jobs/{job_id}/stop')
    def stop_job(job_id: str, request: Request):
        try:
            return request.app.state.manager.stop(job_id)
        except KeyError:
            raise HTTPException(404, 'Không tìm thấy lượt xử lý.') from None

    @app.get('/api/jobs/{job_id}/files/{file_id}')
    def get_file(job_id: str, file_id: str, request: Request, download: bool = False):
        try:
            path, relative = request.app.state.manager.result_path(job_id, file_id)
        except (KeyError, ValueError):
            raise HTTPException(404, 'Quiz chưa sẵn sàng hoặc không tồn tại.') from None
        return FileResponse(path, media_type='application/json',
                            filename=Path(relative).name if download else None)

    @app.get('/api/jobs/{job_id}/download')
    def download(job_id: str, request: Request):
        manager = request.app.state.manager
        try:
            job = manager.snapshot(job_id)
            archive = BytesIO()
            count = 0
            with ZipFile(archive, 'w', ZIP_DEFLATED) as zipfile:
                for file in job['files']:
                    if file['status'] in ('done', 'skipped'):
                        path, relative = manager.result_path(job_id, file['id'])
                        zipfile.write(path, arcname=relative)
                        count += 1
            if not count:
                raise HTTPException(409, 'Chưa có quiz hoàn thành để tải.')
        except (KeyError, ValueError):
            raise HTTPException(404, 'Không tìm thấy kết quả.') from None
        filename = quote(job['name'] + '-quiz.zip')
        return Response(archive.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': f"attachment; filename=quiz-results.zip; filename*=UTF-8''{filename}"})

    static = ROOT / 'static'
    app.mount('/static', StaticFiles(directory=static, check_dir=False), name='static')

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html', media_type='text/html')

    return app
