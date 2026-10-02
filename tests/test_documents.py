import base64
from io import BytesIO
from zipfile import ZipFile

import pymupdf
import pytest
from PIL import Image


def api():
    from quiz_generator import documents
    return documents


def test_discovery_recursive_case_insensitive_and_stable(tmp_path):
    for name in ('Lop7/Toan/Bai12/Tiet36/b.PDF', 'Lop8/a.pptx', 'ignore.txt'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert [str(p.relative_to(tmp_path)) for p in api().discover_documents(tmp_path)] == [
        'Lop7/Toan/Bai12/Tiet36/b.PDF', 'Lop8/a.pptx',
    ]


def test_pdf_preserves_text_and_page_images(tmp_path):
    source = tmp_path / 'lesson.pdf'
    with pymupdf.open() as pdf:
        for text in ('First page: triangle', 'Second page: angles'):
            page = pdf.new_page()
            page.insert_text((40, 40), text)
        pdf.save(source)
    document = api().read_document(source)
    assert 'First page: triangle' in document.text
    assert document.text.index('First page') < document.text.index('Second page')
    assert document.page_count == 2
    assert len(document.images) == 2
    for encoded in document.images:
        Image.open(BytesIO(base64.b64decode(encoded))).verify()
    assert api().read_document(source, text_only=True).images == []


def test_scanned_pdf_requires_images_in_text_only_mode(tmp_path):
    source = tmp_path / 'scan.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.draw_rect(pymupdf.Rect(10, 10, 100, 100))
        pdf.save(source)
    assert len(api().read_document(source).images) == 1
    with pytest.raises(ValueError, match='text|Text'):
        api().read_document(source, text_only=True)


def test_document_limit_rejects_instead_of_truncating(tmp_path):
    source = tmp_path / 'large.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(source)
    with pytest.raises(ValueError, match='2.*1|1.*2'):
        api().read_document(source, max_pages=1)


def test_pptx_slide_order_and_images(tmp_path):
    source = tmp_path / 'slides.pptx'
    image = BytesIO()
    Image.new('RGB', (64, 40), 'green').save(image, format='PNG')
    with ZipFile(source, 'w') as archive:
        for number in (10, 2, 1):
            archive.writestr(f'ppt/slides/slide{number}.xml', f'''<p:sld
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <a:t>Slide {number}</a:t><a:blip r:embed="rId1"/></p:sld>''')
            archive.writestr(f'ppt/slides/_rels/slide{number}.xml.rels', '''<Relationships
                xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId1" Type="image" Target="../media/image.png"/>
                </Relationships>''')
        archive.writestr('ppt/media/image.png', image.getvalue())
    document = api().read_document(source)
    assert document.page_count == 3
    assert document.text.index('Slide 1\n') < document.text.index('Slide 2\n') < document.text.index('Slide 10\n')
    assert len(document.images) == 3


def test_text_and_image_limits_reject_full_document(tmp_path):
    source = tmp_path / 'limits.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 40), 'Lots of text about triangles')
        pdf.save(source)
    with pytest.raises(ValueError, match='text|ký tự'):
        api().read_document(source, max_chars=5)
    with pytest.raises(ValueError, match='ảnh|image'):
        api().read_document(source, max_image_bytes=1)


def test_transparent_pptx_image_preserves_black_content_on_white_background(tmp_path):
    source = tmp_path / 'transparent.pptx'
    png = BytesIO()
    image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(30, 70):
            image.putpixel((x, y), (0, 0, 0, 255))
    image.save(png, format='PNG')
    with ZipFile(source, 'w') as archive:
        archive.writestr('ppt/slides/slide1.xml', '''<sld
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <a:blip r:embed="rId1"/></sld>''')
        archive.writestr('ppt/slides/_rels/slide1.xml.rels', '''<Relationships>
            <Relationship Id="rId1" Target="../media/equation.png"/></Relationships>''')
        archive.writestr('ppt/media/equation.png', png.getvalue())
    document = api().read_document(source)
    with Image.open(BytesIO(base64.b64decode(document.images[0]))) as decoded:
        assert min(decoded.getpixel((5, 5))) > 230
        assert max(decoded.getpixel((50, 50))) < 20

def test_discovery_excludes_nested_result_roots_and_symlink_alias(tmp_path):
    root = tmp_path / 'documents'
    merged = root / 'merged-documents'
    output = root / 'output'
    for path in (
        root / 'source/lesson.pdf',
        merged / 'Toan7/merged.pptx',
        output / 'Toan7/result.pdf',
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    alias = root / 'merged-alias'
    alias.symlink_to(merged, target_is_directory=True)

    found = api().discover_documents(
        root,
        exclude_roots=(output, merged),
    )

    assert [path.relative_to(root).as_posix() for path in found] == [
        'source/lesson.pdf',
    ]
