"""Format generated quizzes for the Pyramid bulk quiz importer."""

from copy import deepcopy
import json
from pathlib import PurePosixPath
import re
import unicodedata


TITLE = re.compile(
    r'^Quiz (?P<subject>.+?) (?P<grade>\d{1,2}) - (?P<lesson_label>.+?): '
    r'(?P<period>.+) - (?P<name>.+)$'
)

SUBJECT_COURSES = {
    # subject: (folder prefix used in QUIZ LAM LAI, supported grades, course-id suffix)
    'toan': ('Toan', range(6, 13), 1),
    'toan hoc': ('Toan', range(6, 13), 1),
    'khoa hoc tu nhien': ('VatLy', range(6, 10), 2),
    'vat li': ('VatLy', range(6, 13), 2),
    'vat ly': ('VatLy', range(6, 13), 2),
    'hoa hoc': ('Hoa', range(6, 13), 3),
    'sinh hoc': ('Sinh', range(6, 13), 4),
    'van': ('Van', range(6, 13), 5),
    'ngu van': ('Van', range(6, 13), 5),
    'lich su': ('Su', range(6, 13), 6),
    'lich su va dia li': ('Su', range(6, 10), 6),
    'lich su va dia ly': ('Su', range(6, 10), 6),
    'dia li': ('DiaLy', range(6, 13), 7),
    'dia ly': ('DiaLy', range(6, 13), 7),
    'tieng anh': ('Anh', range(6, 13), 8),
    'tin hoc': ('Tin', range(6, 13), 9),
    'cong nghe': ('CongNghe', range(6, 13), 10),
    'mi thuat': ('MiThuat', range(6, 13), 11),
    'my thuat': ('MiThuat', range(6, 13), 11),
    'am nhac': ('AmNhac', range(6, 13), 12),
    'giao duc cong dan': ('GDCD', range(6, 10), 13),
    'giao duc kinh te va phap luat': ('GDKTPL', range(10, 13), 13),
}


def _fold(value: str) -> str:
    return ''.join(
        character for character in unicodedata.normalize('NFD', value.casefold())
        if unicodedata.category(character) != 'Mn'
    ).replace('đ', 'd')


def _component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    if not value or value in ('.', '..'):
        raise ValueError('Tên bài không thể dùng làm tên thư mục output.')
    return value


def _node_fold(value: str) -> str:
    value = ''.join(
        character for character in unicodedata.normalize('NFD', value)
        if not '\u0300' <= character <= '\u036f'
    )
    return value.replace('đ', 'd').replace('Đ', 'D').lower()


def _slugify(value: str) -> str:
    return re.sub(r'-{2,}', '-', re.sub(r'[^a-z0-9]+', '-', _node_fold(value)).strip('-'))


def _metadata(quiz: dict) -> dict:
    if not isinstance(quiz, dict):
        raise ValueError('Quiz phải là một object.')
    match = TITLE.fullmatch(quiz.get('title', ''))
    if not match:
        raise ValueError('Không thể xác định khóa học, bài và tiết từ title của quiz.')
    grade = int(match['grade'])
    course_spec = SUBJECT_COURSES.get(_fold(match['subject']))
    if course_spec is None or grade not in course_spec[1]:
        raise ValueError(
            f'Tool upload hàng loạt không hỗ trợ môn/lớp: {match["subject"]} {grade}.'
        )
    period = re.match(r'Tiết\s+(\d+)(?P<label>.*)$', match['period'], re.IGNORECASE)
    if not period:
        raise ValueError('Không thể xác định số tiết từ title của quiz.')
    course, _, course_suffix = course_spec
    lesson_match = re.fullmatch(r'bai[\s_-]*(\d+)', _fold(match['lesson_label']))
    lesson = int(lesson_match.group(1)) if lesson_match else None
    content_name = _component(match['name'])
    lesson_name = content_name if lesson is not None else _component(match['lesson_label'])
    lesson_folder = (
        f'Bài {lesson}_{lesson_name}' if lesson is not None else lesson_name
    )
    period_label = re.sub(r'^[\s:_-]+', '', period['label'])
    return {
        'course': f'{course}{grade}',
        'course_id': grade * 100 + course_suffix,
        'lesson': lesson,
        'lesson_folder': lesson_folder,
        'period': int(period.group(1)),
        'lesson_name': lesson_name,
        'period_name': _component(period_label) if period_label else content_name,
    }


def bulk_upload_identity(quiz: dict) -> str:
    """Return the same database target identity used by the Node importer."""
    metadata = _metadata(quiz)
    if metadata['lesson'] is None:
        lesson_slug = _slugify(metadata['lesson_name'])
    else:
        lesson_slug = f'{metadata["lesson"]}-bai-{metadata["lesson"]}'
        name_slug = _slugify(metadata['lesson_name'])
        if name_slug:
            lesson_slug += '-' + name_slug
    return f'{metadata["course_id"]}|{lesson_slug}|{metadata["period"]}'


def prepare_bulk_upload(quiz: dict) -> tuple[str, dict]:
    """Return a bulk-import relative path and an independently ordered payload."""
    metadata = _metadata(quiz)
    relative_path = PurePosixPath(
        metadata['course'],
        metadata['lesson_folder'],
        f'T{metadata["period"]}_{metadata["period_name"]}',
        'quiz.json',
    ).as_posix()

    payload = deepcopy(quiz)
    payload['type'] = 'LESSON'
    for question_index, question in enumerate(payload.get('questions', ()), 1):
        question['order'] = question_index
        for option_index, option in enumerate(question.get('options', ()), 1):
            option['order'] = option_index
    return relative_path, payload


def generated_quiz_from_bulk_upload(raw: str) -> dict:
    """Remove importer-only fields while verifying their stable ordering."""
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('type') != 'LESSON':
        raise ValueError('Quiz output cần type LESSON.')
    quiz = deepcopy(payload)
    quiz.pop('type')
    questions = quiz.get('questions')
    if not isinstance(questions, list):
        raise ValueError('Quiz output cần danh sách questions.')
    for question_index, question in enumerate(questions, 1):
        if not isinstance(question, dict) or question.pop('order', None) != question_index:
            raise ValueError('Order câu hỏi không hợp lệ.')
        options = question.get('options')
        if not isinstance(options, list):
            raise ValueError('Quiz output cần danh sách options.')
        for option_index, option in enumerate(options, 1):
            if not isinstance(option, dict) or option.pop('order', None) != option_index:
                raise ValueError('Order đáp án không hợp lệ.')
    return quiz
