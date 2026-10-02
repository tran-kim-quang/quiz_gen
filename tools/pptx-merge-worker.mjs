import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import JSZip from 'jszip';
import PptxGenJS from 'pptxgenjs';

function attributes(xmlTag) {
  return Object.fromEntries(
    [...xmlTag.matchAll(/([\w:]+)="([^"]*)"/g)].map(match => [match[1], match[2]]),
  );
}

function resolveRelationshipTarget(sourcePart, target) {
  if (target.startsWith('/')) return target.slice(1);
  return path.posix.normalize(path.posix.join(path.posix.dirname(sourcePart), target));
}

function mimeTypeForImage(filename) {
  const mimeTypes = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
  };
  const extension = path.posix.extname(filename).toLowerCase();
  const mimeType = mimeTypes[extension];
  if (!mimeType) throw new Error('Định dạng ảnh chưa được hỗ trợ: ' + extension);
  return mimeType;
}

export async function countSlides(fileOrBuffer) {
  const buffer = Buffer.isBuffer(fileOrBuffer)
    ? fileOrBuffer
    : await fs.readFile(fileOrBuffer);
  const zip = await JSZip.loadAsync(buffer);
  return Object.keys(zip.files)
    .filter(name => /^ppt\/slides\/slide\d+\.xml$/i.test(name)).length;
}

export async function extractImageSlides(buffer) {
  const zip = await JSZip.loadAsync(buffer);
  const presentationXml = await zip.file('ppt/presentation.xml')?.async('string');
  const presentationRelsXml = await zip.file('ppt/_rels/presentation.xml.rels')?.async('string');
  if (!presentationXml || !presentationRelsXml) {
    throw new Error('PPTX thiếu presentation.xml hoặc relationships');
  }

  const sizeTag = presentationXml.match(/<p:sldSz\b[^>]*>/)?.[0];
  const size = sizeTag ? attributes(sizeTag) : null;
  if (!size?.cx || !size?.cy) throw new Error('Không đọc được kích thước slide');
  const width = Number(size.cx) / 914400;
  const height = Number(size.cy) / 914400;

  const presentationRelationships = new Map();
  for (const match of presentationRelsXml.matchAll(/<Relationship\b[^>]*\/>/g)) {
    const relation = attributes(match[0]);
    if (relation.Type?.endsWith('/slide')) {
      presentationRelationships.set(
        relation.Id,
        resolveRelationshipTarget('ppt/presentation.xml', relation.Target),
      );
    }
  }
  const slideParts = [...presentationXml.matchAll(/<p:sldId\b[^>]*>/g)]
    .map(match => presentationRelationships.get(attributes(match[0])['r:id']))
    .filter(Boolean);
  if (slideParts.length === 0) throw new Error('PPTX không có slide');

  const slides = [];
  for (const slidePart of slideParts) {
    const slideXml = await zip.file(slidePart)?.async('string');
    const relsPart = path.posix.join(
      path.posix.dirname(slidePart),
      '_rels',
      path.posix.basename(slidePart) + '.rels',
    );
    const relsXml = await zip.file(relsPart)?.async('string');
    if (!slideXml || !relsXml) {
      throw new Error('Slide thiếu XML hoặc relationships: ' + slidePart);
    }

    const imageRelationships = new Map();
    for (const match of relsXml.matchAll(/<Relationship\b[^>]*\/>/g)) {
      const relation = attributes(match[0]);
      if (relation.Type?.endsWith('/image')) {
        imageRelationships.set(
          relation.Id,
          resolveRelationshipTarget(slidePart, relation.Target),
        );
      }
    }

    const images = [];
    for (const match of slideXml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g)) {
      const pictureXml = match[0];
      const relationshipId = pictureXml
        .match(/<a:blip\b[^>]*\br:embed="([^"]+)"/)?.[1];
      const transformXml = pictureXml.match(/<a:xfrm\b[\s\S]*?<\/a:xfrm>/)?.[0];
      const offset = transformXml?.match(/<a:off\b[^>]*>/)?.[0];
      const extent = transformXml?.match(/<a:ext\b[^>]*>/)?.[0];
      const imagePart = relationshipId ? imageRelationships.get(relationshipId) : null;
      if (!imagePart || !offset || !extent) {
        throw new Error('Ảnh thiếu relationship hoặc vị trí: ' + slidePart);
      }
      const offsetValues = attributes(offset);
      const extentValues = attributes(extent);
      const imageBuffer = await zip.file(imagePart)?.async('nodebuffer');
      if (!imageBuffer) throw new Error('Không tìm thấy ảnh ' + imagePart);
      images.push({
        data: 'data:' + mimeTypeForImage(imagePart) + ';base64,'
          + imageBuffer.toString('base64'),
        x: Number(offsetValues.x) / 914400,
        y: Number(offsetValues.y) / 914400,
        w: Number(extentValues.cx) / 914400,
        h: Number(extentValues.cy) / 914400,
      });
    }
    if (images.length === 0) throw new Error('Slide không có ảnh: ' + slidePart);
    if (/<p:(?:sp|grpSp|graphicFrame|cxnSp|contentPart)\b/.test(slideXml)) {
      throw new Error('Slide có đối tượng không hỗ trợ; worker chỉ nhận slide image-only: ' + slidePart);
    }
    slides.push(images);
  }
  return { width, height, slides };
}

async function writeMergedPair({ part1, part2, output }) {
  const [part1Buffer, part2Buffer] = await Promise.all([
    fs.readFile(part1),
    fs.readFile(part2),
  ]);
  const [part1Count, part2Count, first, second] = await Promise.all([
    countSlides(part1Buffer),
    countSlides(part2Buffer),
    extractImageSlides(part1Buffer),
    extractImageSlides(part2Buffer),
  ]);
  if (part1Count < 1 || part2Count < 1) {
    throw new Error('Mỗi file part phải có ít nhất một slide');
  }
  if (first.slides.length !== part1Count || second.slides.length !== part2Count) {
    throw new Error('Không đọc được đầy đủ slide ảnh trong file nguồn');
  }
  if (Math.abs(first.width - second.width) > 0.001
      || Math.abs(first.height - second.height) > 0.001) {
    throw new Error('Kích thước slide của part 1 và part 2 không giống nhau');
  }

  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'SOURCE', width: first.width, height: first.height });
  pptx.layout = 'SOURCE';
  pptx.author = 'Quiz Generator';
  pptx.subject = 'Merged image-only lesson slides';
  pptx.title = path.basename(output, '.pptx');
  for (const sourceSlide of [...first.slides, ...second.slides]) {
    const slide = pptx.addSlide();
    slide.background = { color: 'FFFFFF' };
    for (const image of sourceSlide) slide.addImage(image);
  }
  await fs.mkdir(path.dirname(output), { recursive: true });
  await pptx.writeFile({ fileName: output, compression: true });
  const outputSlides = await countSlides(output);
  const expected = part1Count + part2Count;
  if (outputSlides !== expected) {
    throw new Error('Số slide sau khi ghép không đúng: ' + outputSlides + '/' + expected);
  }
  return { part1Slides: part1Count, part2Slides: part2Count, outputSlides };
}

export async function mergePair(args) {
  try {
    return await writeMergedPair(args);
  } catch (error) {
    if (args.output) await fs.rm(args.output, { force: true }).catch(() => {});
    throw error;
  }
}

function cliArguments(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!['--part1', '--part2', '--output'].includes(name) || !value) {
      throw new Error('Cú pháp: --part1 PATH --part2 PATH --output PATH');
    }
    values[name.slice(2)] = value;
  }
  if (!values.part1 || !values.part2 || !values.output) {
    throw new Error('Thiếu --part1, --part2 hoặc --output');
  }
  return values;
}

async function main() {
  try {
    const result = await mergePair(cliArguments(process.argv.slice(2)));
    process.stdout.write(JSON.stringify({ ok: true, ...result }) + '\n');
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stdout.write(JSON.stringify({ ok: false, kind: 'source', error: message }) + '\n');
    process.stderr.write(message + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
