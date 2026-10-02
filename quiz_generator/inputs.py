"""Normalize staging names while retaining a lossless original-path mapping."""

import hashlib
from pathlib import PurePosixPath
import re
import unicodedata


def normalize_name(name: str) -> str:
    value = ''.join(c for c in unicodedata.normalize('NFD', name)
                    if unicodedata.category(c) != 'Mn')
    return value.replace('đ', 'd').replace('Đ', 'D')


def validate_relative_path(path: str) -> tuple[str, ...]:
    if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
            or re.match(r'^[A-Za-z]:', path) or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise ValueError('Đường dẫn input phải là đường dẫn tương đối hợp lệ.')
    parts = tuple(path.split('/'))
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Đường dẫn input chứa cấp thư mục không hợp lệ.')
    if any(not normalize_name(part) or normalize_name(part) in ('.', '..') for part in parts):
        raise ValueError('Tên input không còn ký tự hợp lệ sau khi bỏ dấu.')
    return parts


def build_path_mapping(original_paths: list[str]) -> dict[str, str]:
    """Return normalized → original; disambiguate both files and folders."""
    if len(set(original_paths)) != len(original_paths):
        raise ValueError('Có đường dẫn input trùng nhau.')
    parsed = {path: validate_relative_path(path) for path in original_paths}
    leaves = set(parsed.values())
    prefixes = set()
    for parts in leaves:
        for length in range(1, len(parts) + 1):
            prefix = parts[:length]
            if length < len(parts) and prefix in leaves:
                raise ValueError('Một đường dẫn vừa là file vừa là thư mục.')
            prefixes.add(prefix)
    mapped = {(): ()}
    siblings = {}
    for prefix in sorted(prefixes, key=lambda p: (len(p), p)):
        parent = mapped[prefix[:-1]]
        used = siblings.setdefault(parent, set())
        name = normalize_name(prefix[-1])
        candidate = name
        if candidate.casefold() in used:
            digest = hashlib.sha256('/'.join(prefix).encode('utf-8')).hexdigest()
            extension = PurePosixPath(name).suffix if prefix in leaves else ''
            stem = name[:-len(extension)] if extension else name
            length = 8
            candidate = f'{stem}--{digest[:length]}{extension}'
            while candidate.casefold() in used:
                length += 4
                candidate = f'{stem}--{digest[:length]}{extension}'
        used.add(candidate.casefold())
        mapped[prefix] = parent + (candidate,)
    return {'/'.join(mapped[parts]): original for original, parts in parsed.items()}


def validate_output_paths(original_paths: list[str]) -> None:
    """Reject trees that cannot coexist with JSON filenames and worker state."""
    outputs = {validate_relative_path(path + '.json') for path in original_paths}
    reserved = {'.quiz-state.json', '.quiz-worker.lock'}
    for parts in outputs:
        if parts[0] in reserved:
            raise ValueError('Tên thư mục gốc trùng tên file quản lý output; hãy chọn thư mục bên ngoài nó.')
        for length in range(1, len(parts)):
            if parts[:length] in outputs:
                conflict = '/'.join(parts[:length])
                raise ValueError(f'Không thể giữ cây output: {conflict} vừa là tên quiz vừa là thư mục. '
                                 'Hãy đổi tên file hoặc thư mục trùng trước khi upload.')
