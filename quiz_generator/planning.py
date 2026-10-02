"""Plan physical source files into logical quiz documents."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Literal
import unicodedata

from .inputs import validate_relative_path


@dataclass(frozen=True)
class SourceFile:
    path: Path | None
    normalized_path: str
    original_path: str


@dataclass(frozen=True)
class PartIdentity:
    course: str
    lesson: str
    section: int
    part: int


@dataclass(frozen=True)
class PlanIssue:
    code: str
    message: str
    members: tuple[str, ...]
    fatal: bool = False


@dataclass(frozen=True)
class PlannedDocument:
    source_path: Path | None
    state_key: str
    prompt_path: str
    output_path: str
    kind: Literal['standalone', 'merged']
    action: Literal['standalone', 'merge', 'reuse']
    members: tuple[SourceFile, ...]
    warning: str = ''


@dataclass(frozen=True)
class DocumentPlan:
    documents: tuple[PlannedDocument, ...]
    issues: tuple[PlanIssue, ...]
    merged_root: Path | None = None


def _fold(value: str) -> str:
    normalized = ''.join(
        character for character in unicodedata.normalize('NFD', value)
        if unicodedata.category(character) != 'Mn'
    )
    return normalized.replace('đ', 'd').replace('Đ', 'D')


def _course_and_lesson(parts: tuple[str, ...]) -> tuple[str, str] | None:
    course = next(
        (part for part in parts[:-1] if re.fullmatch(r'toan\s*\d+', _fold(part), re.I)),
        None,
    )
    lesson = next(
        (part for part in parts[:-1] if re.match(r'^bai\s*0*\d+(?:\D|$)', _fold(part), re.I)),
        None,
    )
    return (course, lesson) if course and lesson else None


def parse_part_identity(relative_path: str) -> PartIdentity | None:
    parts = validate_relative_path(relative_path)
    if PurePosixPath(parts[-1]).suffix.casefold() != '.pptx':
        return None
    course_lesson = _course_and_lesson(parts)
    if course_lesson is None:
        return None

    stem = _fold(PurePosixPath(parts[-1]).stem)
    matches = {
        (int(match.group('section')), int(match.group('part')))
        for match in re.finditer(
            r'(?:^|[_\-\s])(?:tiet|t)\s*0*(?P<section>\d+)'
            r'\s*[_\-\s]+\s*(?P<part>[12])(?=$|\D)',
            stem,
            re.I,
        )
    }
    if len(matches) != 1:
        return None
    section, part = next(iter(matches))

    folder_sections = {
        int(match.group('section'))
        for folder in parts[:-1]
        if (match := re.match(
            r'^(?:tiet|t)\s*0*(?P<section>\d+)(?:\D|$)', _fold(folder), re.I
        ))
    }
    if folder_sections and folder_sections != {section}:
        return None
    return PartIdentity(*course_lesson, section, part)

def _sort_key(source: SourceFile) -> tuple[str, str, str]:
    return (
        _fold(source.original_path).casefold(),
        source.original_path,
        source.normalized_path,
    )


def _group_key(identity: PartIdentity) -> tuple[str, str, int]:
    return (
        _fold(identity.course).casefold(),
        _fold(identity.lesson).casefold(),
        identity.section,
    )


def _safe_target(root: Path, relative: str) -> Path:
    validate_relative_path(relative)
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError('Đường dẫn output nằm ngoài thư mục được cấu hình.')
    return target


def standalone_document(source: SourceFile, *, warning: str = '') -> PlannedDocument:
    validate_relative_path(source.normalized_path)
    validate_relative_path(source.original_path)
    identity = parse_part_identity(source.original_path)
    if identity is None:
        output = str(PurePosixPath(source.original_path).with_suffix('.json'))
    else:
        stem = PurePosixPath(source.original_path).stem
        output = f'{identity.course}/{identity.lesson}/T{identity.section}/{stem}.json'
    validate_relative_path(output)
    return PlannedDocument(
        source_path=source.path,
        state_key=source.normalized_path,
        prompt_path=source.normalized_path,
        output_path=output,
        kind='standalone',
        action='standalone',
        members=(source,),
        warning=warning,
    )


def _merged_document(
    entries: list[tuple[SourceFile, PartIdentity]], merged_root: Path
) -> PlannedDocument:
    ordered = sorted(entries, key=lambda entry: (entry[1].part, _sort_key(entry[0])))
    representative = min((entry[1] for entry in entries),
                         key=lambda identity: (
                             _fold(identity.course).casefold(),
                             _fold(identity.lesson).casefold(),
                             identity.course,
                             identity.lesson,
                         ))
    relative_pptx = (
        f'{representative.course}/{representative.lesson}/T{representative.section}/'
        f'T{representative.section}_Slide.pptx'
    )
    source_path = _safe_target(merged_root, relative_pptx)
    output_path = str(PurePosixPath(relative_pptx).with_suffix('.json'))
    processing_path = _fold(relative_pptx)
    return PlannedDocument(
        source_path=source_path,
        state_key=processing_path,
        prompt_path=processing_path,
        output_path=output_path,
        kind='merged',
        action='reuse' if source_path.is_file() else 'merge',
        members=tuple(entry[0] for entry in ordered),
    )


def plan_documents(sources, merged_root: Path) -> DocumentPlan:
    ordered_sources = tuple(sorted(sources, key=_sort_key))
    groups: dict[tuple[str, str, int], list[tuple[SourceFile, PartIdentity]]] = {}
    documents: list[PlannedDocument] = []
    issues: list[PlanIssue] = []

    for source in ordered_sources:
        validate_relative_path(source.normalized_path)
        validate_relative_path(source.original_path)
        identity = parse_part_identity(source.original_path)
        if identity is None:
            warning = ''
            if PurePosixPath(source.original_path).suffix.casefold() == '.pptx':
                warning = 'Không nhận diện được part; xử lý tài liệu độc lập.'
                issues.append(PlanIssue(
                    code='unrecognized_part',
                    message=warning,
                    members=(source.original_path,),
                ))
            documents.append(standalone_document(source, warning=warning))
            continue
        groups.setdefault(_group_key(identity), []).append((source, identity))

    for entries in groups.values():
        part_counts = {
            part: sum(identity.part == part for _, identity in entries)
            for part in (1, 2)
        }
        if len(entries) == 2 and part_counts == {1: 1, 2: 1}:
            documents.append(_merged_document(entries, merged_root))
            continue

        code = 'duplicate_part' if max(part_counts.values()) > 1 else 'missing_part'
        warning = (
            'Bị trùng part; xử lý từng tài liệu độc lập.'
            if code == 'duplicate_part'
            else 'Thiếu part ghép cặp; xử lý tài liệu độc lập.'
        )
        members = tuple(source.original_path for source, _ in sorted(
            entries, key=lambda entry: _sort_key(entry[0])
        ))
        issues.append(PlanIssue(code=code, message=warning, members=members))
        documents.extend(
            standalone_document(source, warning=warning)
            for source, _ in entries
        )

    documents.sort(key=lambda document: (
        _fold(document.output_path).casefold(), document.output_path, document.state_key
    ))
    by_output: dict[str, list[PlannedDocument]] = {}
    for document in documents:
        key = _fold(document.output_path).casefold()
        by_output.setdefault(key, []).append(document)
    for collided in by_output.values():
        if len(collided) < 2:
            continue
        members = tuple(sorted(
            (member.original_path for document in collided for member in document.members),
            key=lambda value: (_fold(value).casefold(), value),
        ))
        issues.append(PlanIssue(
            code='output_collision',
            message=f'Có nhiều tài liệu cùng output {collided[0].output_path}.',
            members=members,
            fatal=True,
        ))

    return DocumentPlan(tuple(documents), tuple(issues), merged_root.resolve())
