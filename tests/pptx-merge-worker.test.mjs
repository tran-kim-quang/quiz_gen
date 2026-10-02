import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

import JSZip from 'jszip';
import PptxGenJS from 'pptxgenjs';

import {
  countSlides,
  extractImageSlides,
  mergePair,
} from '../tools/pptx-merge-worker.mjs';

function svgData(color) {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
    + '<rect width="20" height="10" fill="' + color + '"/></svg>';
  return 'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');
}

async function createDeck(file, { width = 10, height = 5, colors, withText = false }) {
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'TEST', width, height });
  pptx.layout = 'TEST';
  for (const color of colors) {
    const slide = pptx.addSlide();
    if (color) slide.addImage({ data: svgData(color), x: 1, y: 0.5, w: 8, h: 4 });
    if (withText) slide.addText('unsupported text', { x: 1, y: 1, w: 3, h: 1 });
  }
  await pptx.writeFile({ fileName: file });
}

test('mergePair preserves part order, page size, image geometry, and slide counts', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-merge-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { colors: ['#ff0000'] });
    await createDeck(part2, { colors: ['#0000ff'] });

    const result = await mergePair({ part1, part2, output });
    const merged = await extractImageSlides(await readFile(output));

    assert.deepEqual(result, { part1Slides: 1, part2Slides: 1, outputSlides: 2 });
    assert.equal(await countSlides(output), 2);
    assert.equal(merged.width, 10);
    assert.equal(merged.height, 5);
    assert.equal(merged.slides.length, 2);
    assert.deepEqual(
      merged.slides.map(slide => ({ x: slide[0].x, y: slide[0].y, w: slide[0].w, h: slide[0].h })),
      [{ x: 1, y: 0.5, w: 8, h: 4 }, { x: 1, y: 0.5, w: 8, h: 4 }],
    );
    assert.match(Buffer.from(merged.slides[0][0].data.split(',')[1], 'base64').toString(), /#ff0000/);
    assert.match(Buffer.from(merged.slides[1][0].data.split(',')[1], 'base64').toString(), /#0000ff/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

async function outputMissing(file) {
  await assert.rejects(stat(file), error => error.code === 'ENOENT');
}

async function unsupportedImageDeck(source, destination) {
  const zip = await JSZip.loadAsync(await readFile(source));
  const slideXml = await zip.file('ppt/slides/slide1.xml').async('string');
  const relationshipId = slideXml.match(/r:embed="([^"]+)"/)?.[1];
  const relsName = 'ppt/slides/_rels/slide1.xml.rels';
  const relsXml = await zip.file(relsName).async('string');
  const relationship = [...relsXml.matchAll(/<Relationship\b[^>]*\/>/g)]
    .find(match => match[0].includes('Id="' + relationshipId + '"'))?.[0];
  const target = relationship.match(/Target="([^"]+)"/)?.[1];
  const media = 'ppt/media/' + path.posix.basename(target);
  const raw = await zip.file(media).async('nodebuffer');
  const renamed = media.replace(/\.[^.]+$/i, '.bmp');
  zip.file(renamed, raw);
  zip.remove(media);
  zip.file(relsName, relsXml.replace(target, target.replace(/\.[^.]+$/i, '.bmp')));
  await fs.writeFile(destination, await zip.generateAsync({ type: 'nodebuffer' }));
}

test('mergePair rejects presentations with different page sizes', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-size-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { width: 10, colors: ['#ff0000'] });
    await createDeck(part2, { width: 11, colors: ['#0000ff'] });

    await assert.rejects(mergePair({ part1, part2, output }), /Kích thước slide/);
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('mergePair rejects an empty presentation', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-empty-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { colors: [] });
    await createDeck(part2, { colors: ['#0000ff'] });

    await assert.rejects(mergePair({ part1, part2, output }), /không có slide|ít nhất một slide/i);
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('mergePair rejects a slide without pictures', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-no-picture-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { colors: [null], withText: true });
    await createDeck(part2, { colors: ['#0000ff'] });

    await assert.rejects(mergePair({ part1, part2, output }), /không có ảnh/i);
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('mergePair rejects a non-picture shape even when a picture exists', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-shape-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { colors: ['#ff0000'], withText: true });
    await createDeck(part2, { colors: ['#0000ff'] });

    await assert.rejects(mergePair({ part1, part2, output }), /đối tượng|image-only/i);
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('mergePair rejects malformed OOXML and unsupported image extensions', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-invalid-'));
  try {
    const malformed = path.join(directory, 'malformed.pptx');
    const valid = path.join(directory, 'valid.pptx');
    const unsupported = path.join(directory, 'unsupported.pptx');
    const output = path.join(directory, 'merged.pptx');
    await fs.writeFile(malformed, Buffer.from('not a zip'));
    await createDeck(valid, { colors: ['#0000ff'] });
    await unsupportedImageDeck(valid, unsupported);

    await assert.rejects(mergePair({ part1: malformed, part2: valid, output }));
    await outputMissing(output);
    await assert.rejects(
      mergePair({ part1: unsupported, part2: valid, output }),
      /Định dạng ảnh/,
    );
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('CLI emits exactly one source-error JSON object and removes output', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pptx-cli-'));
  try {
    const part1 = path.join(directory, 'part1.pptx');
    const part2 = path.join(directory, 'part2.pptx');
    const output = path.join(directory, 'merged.pptx');
    await createDeck(part1, { colors: ['#ff0000'], withText: true });
    await createDeck(part2, { colors: ['#0000ff'] });

    const result = spawnSync(process.execPath, [
      path.resolve('tools/pptx-merge-worker.mjs'),
      '--part1', part1,
      '--part2', part2,
      '--output', output,
    ], { encoding: 'utf8' });

    assert.notEqual(result.status, 0);
    assert.equal(result.stdout.trim().split('\n').length, 1);
    const response = JSON.parse(result.stdout);
    assert.equal(response.ok, false);
    assert.equal(response.kind, 'source');
    assert.match(response.error, /đối tượng|image-only/i);
    assert.match(result.stderr, /đối tượng|image-only/i);
    await outputMissing(output);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
