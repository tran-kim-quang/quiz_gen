"""Read reference text and images without silently truncating documents."""

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image, ImageOps
import pymupdf

SUPPORTED_SUFFIXES = {'.pdf', '.pptx'}
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'


@dataclass
class Document:
    text: str
    images: list[str]
    page_count: int


def discover_documents(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f'Không tìm thấy thư mục tài liệu: {root}')
    return sorted(
        (p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda p: p.relative_to(root).as_posix(),
    )


def _jpeg(raw: bytes, image_edge: int) -> bytes:
    with Image.open(BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image).convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        image = Image.alpha_composite(background, image).convert('RGB')
        image.thumbnail((image_edge, image_edge))
        output = BytesIO()
        image.save(output, format='JPEG', quality=92)
        return output.getvalue()


def _part_target(base: str, target: str) -> str:
    """Resolve both relative and package-absolute OPC relationship targets."""
    target = target.replace('\\', '/')
    resolved = posixpath.normpath(
        target.lstrip('/') if target.startswith('/') else posixpath.join(base, target)
    )
    if resolved in ('.', '..') or resolved.startswith('../') or resolved.startswith('/'):
        raise ValueError('Đường dẫn quan hệ trong PPTX không hợp lệ.')
    return resolved


def _slides(archive: ZipFile) -> list[str]:
    # PowerPoint can reorder slides without renaming slideN.xml.
    if 'ppt/presentation.xml' in archive.namelist():
        presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
        rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        return [_part_target('ppt', targets[s.attrib[R + 'id']])
                for s in presentation.iter(P + 'sldId')]
    return sorted(
        (n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
        key=lambda n: int(re.search(r'slide(\d+)\.xml', n).group(1)),
    )


def read_document(
    path: Path, *, text_only: bool = False, max_pages: int = 80,
    max_chars: int = 200_000, max_image_bytes: int = 20_000_000,
    image_edge: int = 1600,
) -> Document:
    texts, images = [], []
    total_image_bytes = 0
    has_text = False

    def add_image(raw: bytes):
        nonlocal total_image_bytes
        encoded = base64.b64encode(_jpeg(raw, image_edge)).decode('ascii')
        total_image_bytes += len(encoded)
        if total_image_bytes > max_image_bytes:
            raise ValueError(f'Dữ liệu ảnh vượt giới hạn {max_image_bytes} bytes; tăng --max-image-mb.')
        images.append(encoded)

    def check_pages(count: int):
        if count == 0 or count > max_pages:
            raise ValueError(f'Tài liệu có {count} trang/slide; giới hạn là {max_pages}.')

    suffix = path.suffix.lower()
    if suffix == '.pdf':
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError('PDF có mật khẩu; hãy mở khóa trước khi xử lý.')
            count = len(pdf)
            check_pages(count)
            for number, page in enumerate(pdf, 1):
                text = page.get_text(sort=True).strip()
                has_text |= bool(text)
                texts.append(f'[Trang {number}]\n{text}\n')
                if not text_only:
                    scale = min(2.0, image_edge / max(page.rect.width, page.rect.height))
                    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                    add_image(pixmap.tobytes('png'))
    elif suffix == '.pptx':
        with ZipFile(path) as archive:
            slides = _slides(archive)
            count = len(slides)
            check_pages(count)
            for number, name in enumerate(slides, 1):
                slide = ET.fromstring(archive.read(name))
                text = '\n'.join(t.text or '' for t in slide.iter(A + 't')).strip()
                has_text |= bool(text)
                rel_path = posixpath.join(posixpath.dirname(name), '_rels', posixpath.basename(name) + '.rels')
                rels = ET.fromstring(archive.read(rel_path)) if rel_path in archive.namelist() else []
                targets = {r.attrib['Id']: r.attrib.get('Target', '') for r in rels
                           if r.attrib.get('TargetMode') != 'External'}
                first_image = len(images) + 1
                if not text_only:
                    for blip in slide.iter(A + 'blip'):
                        target = targets.get(blip.attrib.get(R + 'embed'))
                        if target:
                            target = _part_target(posixpath.dirname(name), target)
                            if not target.startswith('ppt/media/'):
                                raise ValueError(f'Ảnh slide {number} có đường dẫn không hỗ trợ.')
                            try:
                                add_image(archive.read(target))
                            except (OSError, KeyError) as error:
                                raise ValueError(f'Không đọc được ảnh slide {number}; hãy xuất PPTX thành PDF.') from error
                image_note = f' (ảnh {first_image}–{len(images)})' if len(images) >= first_image else ''
                texts.append(f'[Slide {number}{image_note}]\n{text}\n')
    else:
        raise ValueError(f'Định dạng không hỗ trợ: {suffix}')

    text = '\n'.join(texts)
    if len(text) > max_chars:
        raise ValueError(f'Nội dung text có {len(text)} ký tự; giới hạn là {max_chars}.')
    if not has_text and not images:
        raise ValueError('Không có text đọc được; bỏ --text-only để model đọc ảnh hoặc bản scan.')
    return Document(text, images, count)
