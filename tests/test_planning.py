from pathlib import Path

import pytest


def api():
    from quiz_generator import planning
    return planning


def test_parses_short_and_prefixed_part_names():
    short = api().parse_part_identity('Toan7/Toan7 pptx/Bài 12/T35_2.pptx')
    prefixed = api().parse_part_identity(
        'Toan7/Toan7 pptx/Bài 12/TOAN7_C4_B12_T36_1_fixed.pptx'
    )

    assert short == api().PartIdentity(course='Toan7', lesson='Bài 12', section=35, part=2)
    assert prefixed == api().PartIdentity(course='Toan7', lesson='Bài 12', section=36, part=1)


def test_rejects_ambiguous_and_folder_mismatched_sections():
    assert api().parse_part_identity(
        'Toan11/Bài 4/Tiết 37/T36_1.pptx'
    ) is None
    assert api().parse_part_identity(
        'Toan7/Toan7 pptx/Bài 12/T36_1_T37_2.pptx'
    ) is None
    assert api().parse_part_identity(
        'Toan7/Toan7 pptx/Bài 12/not-a-part.pptx'
    ) is None


def test_removes_redundant_course_wrapper():
    wrapped = api().parse_part_identity(
        'Toan7/Toan7 pptx/Bài 12/TOAN7_C4_B12_T36_1_fixed.pptx'
    )
    direct = api().parse_part_identity(
        'Toan11/Bài 4/Tiết 37/T37_2.pptx'
    )

    assert (wrapped.course, wrapped.lesson) == ('Toan7', 'Bài 12')
    assert (direct.course, direct.lesson) == ('Toan11', 'Bài 4')

def source(tmp_path, relative, *, original=None, materialize=True):
    path = tmp_path / 'input' / relative
    if materialize:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return api().SourceFile(
        path=path if materialize else None,
        normalized_path=relative,
        original_path=original or relative,
    )


def test_pairs_exactly_one_part_in_part_order(tmp_path):
    part2 = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/T36_2.pptx',
                   original='Toan7/Toan7 pptx/Bài 12/T36_2.pptx')
    part1 = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/T36_1.pptx',
                   original='Toan7/Toan7 pptx/Bài 12/T36_1.pptx')
    merged_root = tmp_path / 'merged'

    plan = api().plan_documents([part2, part1], merged_root)

    assert plan.issues == ()
    assert len(plan.documents) == 1
    document = plan.documents[0]
    assert document.kind == 'merged'
    assert document.action == 'merge'
    assert [member.original_path for member in document.members] == [
        part1.original_path, part2.original_path,
    ]
    assert document.source_path == merged_root / 'Toan7/Bài 12/T36/T36_Slide.pptx'
    assert document.output_path == 'Toan7/Bài 12/T36/T36_Slide.json'


def test_missing_duplicate_and_unrecognized_parts_are_standalone(tmp_path):
    missing = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/T35_1.pptx',
                     original='Toan7/Toan7 pptx/Bài 12/T35_1.pptx')
    first = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/T36_1.pptx',
                   original='Toan7/Toan7 pptx/Bài 12/T36_1.pptx')
    duplicate = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/alt_T36_1.pptx',
                       original='Toan7/Toan7 pptx/Bài 12/alt_T36_1.pptx')
    second = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/T36_2.pptx',
                    original='Toan7/Toan7 pptx/Bài 12/T36_2.pptx')
    unknown = source(tmp_path, 'Toan7/Toan7 pptx/Bai 12/lesson.pptx',
                     original='Toan7/Toan7 pptx/Bài 12/lesson.pptx')

    plan = api().plan_documents([missing, first, duplicate, second, unknown], tmp_path / 'merged')

    assert all(document.kind == 'standalone' for document in plan.documents)
    assert {document.output_path for document in plan.documents} == {
        'Toan7/Bài 12/T35/T35_1.json',
        'Toan7/Bài 12/T36/T36_1.json',
        'Toan7/Bài 12/T36/alt_T36_1.json',
        'Toan7/Bài 12/T36/T36_2.json',
        'Toan7/Toan7 pptx/Bài 12/lesson.json',
    }
    assert {'missing_part', 'duplicate_part', 'unrecognized_part'} <= {
        issue.code for issue in plan.issues
    }
    assert not any(issue.fatal for issue in plan.issues)


def test_normalized_case_and_diacritic_group_is_deterministic(tmp_path):
    sources = [
        source(tmp_path, 'Toan7/A/T36_1.pptx',
               original='Toán7/Toán7 pptx/Bài 12/T36_1.pptx'),
        source(tmp_path, 'Toan7/B/T36_2.pptx',
               original='TOAN7/TOAN7 PPTX/BAI 12/T36_2.pptx'),
    ]

    forward = api().plan_documents(sources, tmp_path / 'merged')
    reverse = api().plan_documents(list(reversed(sources)), tmp_path / 'merged')

    assert len(forward.documents) == 1
    assert forward.documents[0].output_path == reverse.documents[0].output_path
    assert forward.documents[0].members == reverse.documents[0].members


def test_maps_merged_and_standalone_outputs(tmp_path):
    part1 = source(tmp_path, 'Toan7/Bai 12/T36_1.pptx',
                   original='Toan7/Bài 12/T36_1.pptx')
    part2 = source(tmp_path, 'Toan7/Bai 12/T36_2.pptx',
                   original='Toan7/Bài 12/T36_2.pptx')
    pdf = source(tmp_path, 'Toan7/Bai 12/worksheet.pdf',
                 original='Toan7/Bài 12/worksheet.pdf')
    merged_root = tmp_path / 'merged'

    first = api().plan_documents([part1, part2, pdf], merged_root)
    merged = next(document for document in first.documents if document.kind == 'merged')
    standalone = next(document for document in first.documents if document.kind == 'standalone')
    merged.source_path.parent.mkdir(parents=True)
    merged.source_path.touch()
    second = api().plan_documents([part1, part2, pdf], merged_root)

    assert standalone.output_path == 'Toan7/Bài 12/worksheet.json'
    assert next(document for document in second.documents if document.kind == 'merged').action == 'reuse'


def test_detects_logical_output_collisions(tmp_path):
    pdf = source(tmp_path, 'Toan7/Bai 12/notes.pdf', original='Toan7/Bài 12/notes.pdf')
    pptx = source(tmp_path, 'Toan7/Bai 12/notes.pptx', original='Toan7/Bài 12/notes.pptx')

    plan = api().plan_documents([pdf, pptx], tmp_path / 'merged')

    collisions = [issue for issue in plan.issues if issue.code == 'output_collision']
    assert len(collisions) == 1
    assert collisions[0].fatal is True
    assert collisions[0].members == (pdf.original_path, pptx.original_path)


def test_rejects_paths_that_escape_roots(tmp_path):
    unsafe = api().SourceFile(
        path=tmp_path / 'evil.pptx',
        normalized_path='../evil.pptx',
        original_path='../evil.pptx',
    )

    with pytest.raises(ValueError, match='đường dẫn|Đường dẫn'):
        api().plan_documents([unsafe], tmp_path / 'merged')
