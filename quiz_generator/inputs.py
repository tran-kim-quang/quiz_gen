"""Normalize staging names while retaining a lossless original-path mapping."""

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
import unicodedata


@dataclass(frozen=True)
class SourcePlan:
    """One LLM generation unit and its lossless output location."""

    key: str
    path: str
    output: str
    normalized_output: str
    validation_path: str
    relative_paths: tuple[str, ...]
    original_paths: tuple[str, ...]


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


def _natural_key(value: str):
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r'(\d+)', value))


def _period_number(plan: SourcePlan):
    for value in (plan.key, *plan.relative_paths):
        for part in reversed(PurePosixPath(value).parts):
            match = re.match(r'^t(?:iet)?[\s_-]*(\d+)(?:\D|$)', part, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def _validate_result_paths(paths: list[str]) -> None:
    parsed = [validate_relative_path(path) for path in paths]
    parsed_set = set(parsed)
    if len(parsed_set) != len(parsed):
        raise ValueError('Nhiều folder lá tạo ra cùng một đường dẫn output.')
    reserved = {'.quiz-state.json', '.quiz-worker.lock'}
    for parts in parsed:
        if parts[0] in reserved:
            raise ValueError('Tên output trùng file quản lý của ứng dụng.')
        for length in range(1, len(parts)):
            if parts[:length] in parsed_set:
                raise ValueError('Cây output có đường dẫn vừa là file vừa là thư mục.')


def plan_source_units(normalized_paths, path_mapping=None, *, group_leaf_folders=True,
                      root_name='documents') -> list[SourcePlan]:
    """Plan one unit per leaf folder, or one unit per file for single-file mode."""
    normalized_paths = tuple(normalized_paths)
    mapping = dict(path_mapping) if path_mapping is not None else {path: path for path in normalized_paths}
    if set(mapping) != set(normalized_paths):
        raise ValueError('Mapping đường dẫn không khớp tài liệu nguồn.')
    for normalized, original in mapping.items():
        validate_relative_path(normalized)
        validate_relative_path(original)

    plans = []
    if not group_leaf_folders:
        for normalized in normalized_paths:
            original = mapping[normalized]
            plans.append(SourcePlan(normalized, original, original + '.json', normalized + '.json',
                                    normalized, (normalized,), (original,)))
    else:
        parents = {PurePosixPath(path).parent for path in normalized_paths}
        non_leaves = set()
        root_parent = PurePosixPath('.')
        if root_parent in parents and any(parent.parts for parent in parents):
            non_leaves.add(root_parent)
        for parent in parents:
            for length in range(1, len(parent.parts)):
                ancestor = PurePosixPath(*parent.parts[:length])
                if ancestor in parents:
                    non_leaves.add(ancestor)
        leaves = parents - non_leaves
        if len(leaves) != len(parents):
            raise ValueError('Tài liệu phải nằm trong folder cuối cùng, không đặt trực tiếp ở folder còn có folder con.')
        for parent in leaves:
            relatives = tuple(path for path in normalized_paths if PurePosixPath(path).parent == parent)
            originals = tuple(mapping[path] for path in relatives)
            original_parents = {PurePosixPath(path).parent for path in originals}
            if len(original_parents) != 1:
                raise ValueError('Các tài liệu trong một folder xử lý không cùng folder gốc.')
            original_parent = next(iter(original_parents))
            normalized_leaf = parent.name if parent.parts else normalize_name(root_name)
            original_leaf = original_parent.name if original_parent.parts else root_name
            key = parent.as_posix() if parent.parts else normalize_name(root_name)
            display = original_parent.as_posix() if original_parent.parts else root_name
            output = (original_parent / f'{original_leaf}.json').as_posix() if original_parent.parts else f'{root_name}.json'
            normalized_output = (parent / f'{normalized_leaf}.json').as_posix() if parent.parts else f'{normalize_name(root_name)}.json'
            plans.append(SourcePlan(key, display, output, normalized_output, relatives[0], relatives, originals))

    plans.sort(key=lambda plan: (
        1 if _period_number(plan) is None else 0,
        _period_number(plan) or 0,
        _natural_key(plan.key),
    ))
    _validate_result_paths([plan.output for plan in plans])
    return plans
