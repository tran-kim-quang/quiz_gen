"use strict";

const icons = {
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  folder: '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  'folder-tree': '<path d="M3 3v16M3 7h5M3 17h5M10 4h3l2 2h6v5H10ZM10 14h3l2 2h6v5H10Z"/>',
  sparkles: '<path d="m12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2ZM20 2v4M18 4h4"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-left-right': '<path d="M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  'shield-check': '<path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
  book: '<path d="M4 4h7l1 2 1-2h7v16h-7l-1 1-1-1H4ZM12 6v15M7 8h2M15 8h2"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>',
  refresh: '<path d="M20 7a8 8 0 1 0 1 7M20 3v5h-5"/>',
  'list-check': '<path d="m3 5 2 2 3-4M11 5h10M3 12h4M11 12h10M3 19h4M11 19h10"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  square: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>'
};
function icon(name) {
  const holder = document.createElement('span');
  // SVG markup is from the fixed local icon set; input names always use textContent.
  holder.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
  return holder.firstElementChild;
}
document.querySelectorAll('[data-icon]').forEach(node => node.append(icon(node.dataset.icon)));
const $ = id => document.getElementById(id);
const state = {kind: 'file', files: [], skippedFiles: [], preview: null, view: 'original', config: null,
  job: null, jobs: [], busy: false, uploading: false, previewSequence: 0, pollTimer: null};
const activeStatuses = new Set(['queued', 'running', 'stopping']);
const labels = {queued: 'Đang chờ trong hàng đợi', running: 'Đang xử lý', stopping: 'Đang dừng',
  completed: 'Hoàn tất', completed_with_errors: 'Hoàn tất, có lỗi', failed: 'Lỗi',
  stopped: 'Đã dừng', interrupted: 'Đã gián đoạn', pending: 'Đang chờ',
  reading: 'Đang đọc', generating: 'Đang tạo', done: 'Đã tạo', skipped: 'Đã có quiz'};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
function sizeLabel(bytes) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function renderSkippedFiles() {
  const container = $('skipped-files');
  const list = $('skipped-files-list');
  list.replaceChildren();
  container.hidden = !state.skippedFiles.length;
  $('skipped-files-count').textContent = state.skippedFiles.length ? `${state.skippedFiles.length} file` : '';
  state.skippedFiles.forEach(path => {
    const item = node('li');
    item.title = path;
    item.append(icon('x'), node('span', '', path));
    list.append(item);
  });
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  let result;
  try { result = await response.json(); } catch { throw new Error('Không đọc được phản hồi từ ứng dụng.'); }
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Yêu cầu chưa thành công (${response.status}).`);
  return result;
}
function updateControls() {
  const locked = state.uploading;
  ['file-tab', 'folder-tab', 'choose-button', 'reset-button', 'file-input', 'folder-input'].forEach(id => $(id).disabled = locked);
  $('start-button').disabled = locked || !state.preview || !state.config?.configured;
  if (!state.uploading) $('start-label').textContent = state.busy ? 'Thêm vào hàng đợi' : 'Bắt đầu tạo quiz';
}
function setMode(kind) {
  if (state.uploading) return;
  state.kind = kind;
  clearSelection();
  const folder = kind === 'folder';
  $('file-tab').setAttribute('aria-selected', String(!folder));
  $('folder-tab').setAttribute('aria-selected', String(folder));
  $('source-picker').setAttribute('aria-labelledby', folder ? 'folder-tab' : 'file-tab');
  $('picker-heading').textContent = folder ? 'Mang cả thư mục bài học vào đây' : 'Thả tài liệu vào đây';
  $('picker-description').textContent = folder ? 'Tự lấy tài liệu trong tất cả thư mục con' : 'hoặc chọn một file từ máy tính';
  $('choose-label').replaceChildren(document.createTextNode(folder ? 'Chọn thư mục ' : 'Chọn file '), icon('arrow-right'));
  $('picker-limit').textContent = folder ? 'PDF & PPTX · Không giới hạn số lượng' : 'PDF hoặc PPTX · Không giới hạn dung lượng ứng dụng';
}
function clearSelection() {
  state.previewSequence++;
  state.files = []; state.skippedFiles = []; state.preview = null;
  $('file-input').value = ''; $('folder-input').value = '';
  $('selection-summary').textContent = 'Chưa chọn tài liệu';
  $('selection-size').textContent = 'Một file hoặc một thư mục chứa nhiều bài học';
  $('selection-note').textContent = ''; $('selection-check').hidden = true;
  renderSkippedFiles();
  notice(); renderTree(); updateControls();
}
async function selectFiles(files) {
  if (state.uploading) return;
  const sequence = ++state.previewSequence;
  notice(); state.preview = null;
  const all = Array.from(files);
  const supported = all.filter(file => /\.(pdf|pptx)$/i.test(file.name));
  state.skippedFiles = all.filter(file => !/\.(pdf|pptx)$/i.test(file.name))
    .map(file => file.webkitRelativePath || file.name);
  renderSkippedFiles();
  if (!supported.length) {
    state.files = []; state.preview = null;
    $('selection-summary').textContent = 'Không có tài liệu hỗ trợ';
    $('selection-size').textContent = `${state.skippedFiles.length} file không phải PDF/PPTX`;
    $('selection-note').textContent = 'Toàn bộ file đã bị bỏ qua.';
    $('selection-check').hidden = true;
    renderTree(); updateControls();
    notice('Nguồn chưa có tài liệu PDF hoặc PPTX để tạo quiz.');
    return;
  }
  if (state.kind === 'file' && all.length !== 1) { clearSelection(); notice('Chế độ Một file chỉ nhận một tài liệu mỗi lượt.'); return; }
  const bytes = supported.reduce((total, file) => total + file.size, 0);
  state.files = supported;
  $('selection-summary').textContent = `${supported.length} tài liệu đã chọn`;
  $('selection-size').textContent = `${sizeLabel(bytes)} · ${state.kind === 'folder' ? supported[0].webkitRelativePath.split('/')[0] : supported[0].name}`;
  $('selection-note').textContent = state.skippedFiles.length ? `Đã bỏ qua ${state.skippedFiles.length} file không phải PDF/PPTX.` : '';
  $('selection-check').hidden = false;
  updateControls();
  try {
    const paths = supported.map(file => state.kind === 'folder' ? file.webkitRelativePath : file.name);
    const preview = await api('/api/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind: state.kind, paths})});
    if (sequence !== state.previewSequence) return;
    state.preview = preview;
    renderTree(); updateControls();
  } catch (error) {
    if (sequence === state.previewSequence) { state.preview = null; notice(error.message); updateControls(); }
  }
}
function renderTree() {
  $('original-view').classList.toggle('active', state.view === 'original');
  $('normalized-view').classList.toggle('active', state.view === 'normalized');
  $('tree-caption').textContent = state.view === 'original' ? 'ĐẦU RA' : 'INPUT KHÔNG DẤU';
  const container = $('path-tree');
  container.replaceChildren();
  const files = state.preview?.files || state.job?.files;
  if (!files?.length) {
    const empty = node('div', 'tree-empty'); const mark = node('span'); mark.append(icon('folder-tree'));
    empty.append(mark, node('strong', '', 'Cây thư mục của bạn'), node('p', '', 'Chọn nguồn để xem trước vị trí của từng file quiz.'));
    container.append(empty); return;
  }
  const root = new Map();
  files.slice(0, 200).forEach(file => {
    const path = state.view === 'normalized' ? file.normalized_path : file.path + '.json';
    const parts = path.split('/'); let children = root;
    parts.forEach((part, index) => {
      if (!children.has(part)) children.set(part, {name: part, file: index === parts.length - 1, children: new Map()});
      children = children.get(part).children;
    });
  });
  function list(children, depth) {
    const ul = node('ul', `tree-list${depth === 0 ? ' root' : ''}`);
    children.forEach(item => {
      const li = node('li'); const row = node('div', `tree-node${item.file ? ' file' : ''}${depth === 0 ? ' root-node' : ''}`);
      row.title = item.name; row.append(icon(item.file ? 'file' : 'folder'), node('span', '', item.name)); li.append(row);
      if (item.children.size) li.append(list(item.children, depth + 1));
      ul.append(li);
    });
    return ul;
  }
  container.append(list(root, 0));
  if (files.length > 200) container.append(node('p', 'tree-overflow', `Xem trước 200/${files.length} tài liệu. Toàn bộ tài liệu vẫn được xử lý.`));
}
function upload(form) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest(); request.open('POST', '/api/jobs');
    request.upload.onprogress = event => { if (event.lengthComputable) $('start-label').textContent = `Đang tải tài liệu ${Math.round(event.loaded / event.total * 100)}%`; };
    request.onerror = () => reject(new Error('Không kết nối được ứng dụng. Kiểm tra server và thử lại.'));
    request.onload = () => {
      let body; try { body = JSON.parse(request.responseText); } catch { reject(new Error('Không đọc được phản hồi upload.')); return; }
      if (request.status >= 200 && request.status < 300) resolve(body);
      else reject(new Error(typeof body.detail === 'string' ? body.detail : 'Không tải được tài liệu.'));
    };
    request.send(form);
  });
}
async function startJob() {
  if (!state.preview || state.uploading) return;
  state.uploading = true; notice(); updateControls(); $('start-label').textContent = 'Đang tải tài liệu…';
  const form = new FormData(); form.append('kind', state.kind);
  form.append('paths', JSON.stringify(state.preview.files.map(file => file.path)));
  state.files.forEach(file => form.append('files', file, file.name));
  try {
    const job = await upload(form); state.job = job;
    remember(job.id); state.busy = true; clearSelection(); renderJob();
    $('results-panel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    await loadJobs(); schedulePoll();
  } catch (error) { notice(error.message); await loadJobs().catch(() => {}); }
  finally { state.uploading = false; updateControls(); }
}
function remember(id) { try { localStorage.setItem('quiz-studio-job', id); } catch {} }
function lastJobId() { try { return localStorage.getItem('quiz-studio-job'); } catch { return null; } }
function statusBadge(status) { return node('span', `status-badge ${status}`, labels[status] || status); }
function renderJob() {
  const job = state.job;
  $('results-empty').hidden = Boolean(job); $('job-content').hidden = !job;
  if (!job) return;
  $('job-name').textContent = job.name;
  $('job-status').textContent = job.status === 'queued' && job.queue_position
    ? `${labels.queued} · vị trí ${job.queue_position}` : labels[job.status] || job.status;
  $('job-status').className = `status-badge ${job.status}`;
  $('total-count').textContent = job.counts.total; $('done-count').textContent = job.counts.done; $('failed-count').textContent = job.counts.failed;
  const processed = job.counts.done + job.counts.failed;
  const percent = Math.round(processed / job.counts.total * 100);
  $('progress-percent').textContent = `${percent}%`; $('progress-fill').style.width = `${percent}%`;
  $('progress-track').setAttribute('aria-valuenow', String(percent));
  const current = job.files.find(file => ['reading', 'generating'].includes(file.status));
  $('progress-label').textContent = current ? `${labels[current.status]}: ${current.path.split('/').pop()}`
    : job.status === 'queued' ? `Đang chờ đến lượt${job.queue_position ? ` · vị trí ${job.queue_position}` : ''}`
    : `${processed}/${job.counts.total} tài liệu đã xử lý${job.status === 'stopping' ? ' · Chờ file hiện tại hoàn thành' : ''}`;
  $('job-error').hidden = !job.error; $('job-error').textContent = job.error || '';
  $('stop-button').hidden = !activeStatuses.has(job.status); $('stop-button').disabled = job.status === 'stopping';
  $('stop-button').replaceChildren(icon('square'), document.createTextNode(job.status === 'queued' ? 'Huỷ lượt chờ' : 'Dừng sau file này'));
  const zip = $('zip-download');
  zip.setAttribute('aria-disabled', String(!job.counts.done)); zip.tabIndex = job.counts.done ? 0 : -1;
  if (job.counts.done) zip.href = `/api/jobs/${job.id}/download`; else zip.removeAttribute('href');
  const tbody = $('file-table').querySelector('tbody'); tbody.replaceChildren();
  job.files.forEach(file => {
    const tr = node('tr'); const source = node('td'); const cell = node('div', 'file-cell');
    const symbol = node('span', 'file-symbol'); symbol.append(icon('file'));
    const details = node('div'); const parts = file.path.split('/');
    details.append(node('strong', 'file-name', parts.pop()), node('span', 'file-parent', parts.join('/') || 'File đơn'));
    if (file.error) details.append(node('p', 'file-error', file.error));
    cell.append(symbol, details); source.append(cell);
    const status = node('td'); status.append(statusBadge(file.status));
    const actions = node('td'); const links = node('div', 'file-actions');
    if (['done', 'skipped'].includes(file.status)) {
      const view = node('button', 'text-button'); view.append(icon('eye'), document.createTextNode('Xem quiz')); view.addEventListener('click', () => viewQuiz(job.id, file.id));
      const download = node('a', 'text-button', 'JSON'); download.append(icon('download'));
      download.href = `/api/jobs/${job.id}/files/${file.id}?download=true`; download.setAttribute('aria-label', 'Tải JSON');
      links.append(view, download);
    } else links.append(node('span', 'pending-result', '—'));
    actions.append(links); tr.append(source, status, actions); tbody.append(tr);
  });
  $('step-source').className = 'step finished';
  $('step-process').className = `step ${activeStatuses.has(job.status) ? 'active' : 'finished'}`;
  $('step-results').className = `step ${job.counts.done ? 'active' : ''}`;
  if (!state.preview) renderTree();
}
async function viewQuiz(jobId, fileId) {
  try {
    const quiz = await api(`/api/jobs/${jobId}/files/${fileId}`);
    $('quiz-title').textContent = quiz.title; $('quiz-description').textContent = quiz.description;
    const container = $('quiz-questions'); container.replaceChildren();
    quiz.questions.forEach((question, index) => {
      const article = node('article', 'quiz-question'); article.append(node('span', 'question-number', `Câu ${index + 1}`), node('p', 'question-prompt', question.prompt));
      const options = node('div', 'quiz-options');
      question.options.forEach((option, optionIndex) => {
        const row = node('div', `quiz-option${option.isCorrect ? ' correct' : ''}`);
        row.append(node('span', 'option-letter', 'ABCD'[optionIndex]), node('span', '', option.text)); options.append(row);
      });
      const explanation = node('p', 'question-explanation'); explanation.append(node('strong', '', 'Giải thích: '), document.createTextNode(question.explanation));
      article.append(options, explanation); container.append(article);
    });
    $('modal-download').href = `/api/jobs/${jobId}/files/${fileId}?download=true`;
    $('quiz-dialog').showModal(); $('quiz-dialog').scrollTop = 0;
  } catch (error) { notice(error.message); }
}
async function loadJobs() {
  state.jobs = await api('/api/jobs'); state.busy = state.jobs.some(job => activeStatuses.has(job.status));
  $('history-count').textContent = state.jobs.length;
  const recent = $('recent-jobs'); recent.replaceChildren();
  if (!state.jobs.length) recent.append(node('p', 'recent-empty', 'Các lượt xử lý sẽ được lưu ở đây.'));
  state.jobs.slice(0, 5).forEach(job => {
    const button = node('button', `recent-job${job.id === state.job?.id ? ' active' : ''}`);
    button.title = `${job.name} · ${labels[job.status]}`;
    button.append(icon(job.kind === 'folder' ? 'folder' : 'file'), node('span', '', job.name), node('span', 'small-dot'));
    button.addEventListener('click', async () => {
      state.job = await api('/api/jobs/' + job.id); remember(job.id); renderJob(); schedulePoll();
    }); recent.append(button);
  });
  updateControls();
}
function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (!state.job || (!activeStatuses.has(state.job.status) && !state.busy)) return;
  state.pollTimer = setTimeout(async () => {
    try { state.job = await api('/api/jobs/' + state.job.id); renderJob(); await loadJobs(); }
    catch (error) { notice(error.message); }
    schedulePoll();
  }, 1000);
}
$('file-tab').addEventListener('click', () => setMode('file'));
$('folder-tab').addEventListener('click', () => setMode('folder'));
$('choose-button').addEventListener('click', () => $(state.kind === 'folder' ? 'folder-input' : 'file-input').click());
$('file-input').addEventListener('change', event => selectFiles(event.target.files));
$('folder-input').addEventListener('change', event => selectFiles(event.target.files));
$('choose-button').addEventListener('dragover', event => { event.preventDefault(); if (!state.busy) $('choose-button').classList.add('drag-over'); });
$('choose-button').addEventListener('dragleave', () => $('choose-button').classList.remove('drag-over'));
$('choose-button').addEventListener('drop', event => {
  event.preventDefault(); $('choose-button').classList.remove('drag-over');
  if (state.kind === 'folder') notice('Dùng nút Chọn thư mục để giữ đầy đủ các cấp thư mục con.');
  else selectFiles(event.dataTransfer.files);
});
$('original-view').addEventListener('click', () => { state.view = 'original'; renderTree(); });
$('normalized-view').addEventListener('click', () => { state.view = 'normalized'; renderTree(); });
$('reset-button').addEventListener('click', clearSelection);
$('start-button').addEventListener('click', startJob);
$('stop-button').addEventListener('click', async () => {
  try { state.job = await api(`/api/jobs/${state.job.id}/stop`, {method: 'POST'}); renderJob(); schedulePoll(); }
  catch (error) { notice(error.message); }
});
$('help-button').addEventListener('click', () => $('help-dialog').showModal());
$('close-help').addEventListener('click', () => $('help-dialog').close());
$('close-quiz').addEventListener('click', () => $('quiz-dialog').close());
['help-dialog', 'quiz-dialog'].forEach(id => $(id).addEventListener('click', event => { if (event.target === $(id)) { const box = $(id).getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) $(id).close(); } }));
async function init() {
  try {
    state.config = await api('/api/config');
    $('model-name').textContent = state.config.model || 'Chưa chọn model';
    $('model-caption').textContent = state.config.configured ? 'Đã cấu hình' : 'Thiếu cấu hình';
    $('model-pill').classList.toggle('unconfigured', !state.config.configured);
    if (!state.config.configured) notice('Điền OLLAMA_API_KEY và LLM_MODEL_NAME trong file .env rồi tải lại giao diện.');
    await loadJobs();
    const current = state.jobs.find(job => ['running', 'stopping'].includes(job.status))
      || state.jobs.filter(job => job.status === 'queued').sort((a, b) => a.queue_position - b.queue_position)[0]
      || state.jobs.find(job => job.id === lastJobId());
    if (current) { state.job = current; renderJob(); schedulePoll(); }
    updateControls();
  } catch (error) { notice(error.message); }
}
init();
