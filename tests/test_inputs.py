import unicodedata

import pytest


def api():
    from quiz_generator import inputs
    return inputs


def test_normalizes_vietnamese_without_changing_case_or_spaces():
    assert api().normalize_name('Đại số - Lớp 7') == 'Dai so - Lop 7'
    assert api().normalize_name('BÀI 12.pdf') == 'BAI 12.pdf'
    assert api().normalize_name(unicodedata.normalize('NFD', 'Tiết 36')) == 'Tiet 36'


def test_mapping_restores_exact_unicode_path_and_folder_depth():
    original = 'Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề kiểm tra.pdf'
    decomposed = unicodedata.normalize('NFD', 'Lớp 8') + '/Bài học.PDF'
    mapping = api().build_path_mapping([original, decomposed])
    assert mapping['Lop 7/Mon/Toan/Bai 12/Tiet 36/De kiem tra.pdf'] == original
    assert mapping['Lop 8/Bai hoc.PDF'] == decomposed


def test_accent_collisions_have_unique_paths_without_overwriting():
    originals = ['Lớp 7/Má/Đề.pdf', 'Lớp 7/Ma/Đề.pdf', 'Lớp 7/Má/De.pdf', 'Lớp 7/Má/Bài.pptx']
    mapping = api().build_path_mapping(originals)
    assert len(mapping) == 4
    assert set(mapping.values()) == set(originals)
    assert len({path.casefold() for path in mapping}) == 4
    assert all(not any(unicodedata.combining(c) for c in unicodedata.normalize('NFD', path)) for path in mapping)
    assert all(path.endswith(('.pdf', '.pptx')) for path in mapping)
    assert api().build_path_mapping(list(reversed(originals))) == mapping
    # Both files from Má must remain in one normalized parent folder.
    parents = {path.rsplit('/', 1)[0] for path, original in mapping.items() if '/Má/' in original}
    assert len(parents) == 1


@pytest.mark.parametrize('path', ['../a.pdf', '/tmp/a.pdf', 'a/../b.pdf', 'a//b.pdf', 'C:/a.pdf',
                                 'a\\b.pdf', 'a/./b.pdf', 'a\x00.pdf', 'a\n.pdf', ''])
def test_rejects_unsafe_relative_paths(path):
    with pytest.raises(ValueError):
        api().build_path_mapping([path])


def test_rejects_duplicate_upload_paths_and_file_directory_conflicts():
    with pytest.raises(ValueError):
        api().build_path_mapping(['Root/a.pdf', 'Root/a.pdf'])
    with pytest.raises(ValueError):
        api().build_path_mapping(['Root/a.pdf', 'Root/a.pdf/b.pdf'])
