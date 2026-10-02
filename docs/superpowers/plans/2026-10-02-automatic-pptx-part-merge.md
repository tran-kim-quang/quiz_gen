# Automatic PPTX Part Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically merge valid PPTX part 1/part 2 lessons into one shared class/lesson/section document before quiz generation in CLI, watch, and website flows, while processing irregular files independently.

**Architecture:** Add a pure document planner that separates physical inputs from logical quiz paths, a Python materialization adapter that invokes a single-pair Node worker under a per-output lock, and teach `QuizRunner` to consume planned documents. CLI/watch and web jobs use the same planner and materializer; the web preview uses the pure planning half without writing files.

**Tech Stack:** Python 3.10+, FastAPI/Starlette, pytest, Node.js 20+, `jszip` 3.10.2, `pptxgenjs` 4.0.1, Node test runner, browser JavaScript/CSS.

**Spec:** `docs/superpowers/specs/2026-10-02-automatic-pptx-part-merge-design.md`

## Global Constraints

- Automatic part discovery is the default; there is no opt-in merge mode.
- A merge group requires exactly one part 1 and one part 2 for one normalized class/lesson/section key.
- Missing, duplicate, mismatched, or unrecognized parts run independently and do not block unrelated inputs.
- Shared merged output defaults to `/home/meconlonton/Documents/quiz/merged-documents`; JSON output defaults to `/home/meconlonton/Documents/quiz/documents/output`.
- An existing merged PPTX is authoritative: never compare its sources, validate it during planning, overwrite it, or rebuild it automatically.
- The worker must reuse the image-only `jszip`/`pptxgenjs` engine from `pyramid-upload-to-r2`; do not add `python-pptx`.
- Require Node.js 20 or newer and pin `jszip` to `3.10.2` and `pptxgenjs` to `4.0.1`.
- Pass subprocess arguments as an array with `shell=False`; reject any resolved path outside configured roots.
- Source-specific worker failures fall back to standalone documents; missing runtime/dependencies, timeout, or invalid protocol are fatal infrastructure failures.
- Preserve the existing FIFO web queue and stop only between final logical documents.
- Preserve the user's existing `.env.example` edits while adding the three `QUIZ_*` path examples.

## Review Focus

- Two names that normalize to the same case/diacritic-free group must pair deterministically without making two logical outputs; Task 1 pins this behavior.
- A symlinked or traversal-like source/output must never escape the configured roots; Tasks 1 and 3 pin validation at planning and persistence boundaries.
- A worker that exits successfully but emits extra/malformed stdout must be treated as infrastructure failure, never as a source fallback; Task 3 pins this protocol boundary.
- Two processes targeting the same absent merged PPTX must leave one complete output and make the waiter reuse it; Task 3 pins the recheck-after-lock behavior.
- Restarting with durable job metadata left in `merging` must mark the job interrupted and reset that row safely; Task 6 pins restart recovery.

---

### Task 1: Pure Source Recognition and Document Planning

**Files:**
- Create: `quiz_generator/planning.py`
- Create: `tests/test_planning.py`

**Interfaces:**
- Produces: `SourceFile(path: Path | None, normalized_path: str, original_path: str)`.
- Produces: `PartIdentity(course: str, lesson: str, section: int, part: int)`.
- Produces: `PlanIssue(code: str, message: str, members: tuple[str, ...], fatal: bool = False)`.
- Produces: `PlannedDocument(source_path: Path | None, state_key: str, prompt_path: str, output_path: str, kind: Literal['standalone', 'merged'], action: Literal['standalone', 'merge', 'reuse'], members: tuple[SourceFile, ...], warning: str = '')`, where `output_path` is the complete relative JSON path including `.json`.
- Produces: `DocumentPlan(documents: tuple[PlannedDocument, ...], issues: tuple[PlanIssue, ...])`.
- Produces: `parse_part_identity(relative_path: str) -> PartIdentity | None`.
- Produces: `plan_documents(sources: Sequence[SourceFile], merged_root: Path) -> DocumentPlan`.
- Produces: `standalone_document(source: SourceFile, *, warning: str = '') -> PlannedDocument` for Task 3 fallback.

- [ ] **Step 1: Write failing parser and normalization tests**

Add tests named `test_parses_short_and_prefixed_part_names`, `test_rejects_ambiguous_and_folder_mismatched_sections`, and `test_removes_redundant_course_wrapper`. Assert that `T35_2.pptx` and `TOAN7_C4_B12_T36_1_fixed.pptx` produce the exact section/part identities, `Toan7/Toan7 pptx/Bài 12/...` becomes course `Toan7` and lesson `Bài 12`, and conflicting section evidence returns no merge identity.

- [ ] **Step 2: Run the parser tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_planning.py -k 'parses or rejects or wrapper' -v`

Expected: FAIL because `quiz_generator.planning` does not exist.

- [ ] **Step 3: Implement path parsing and immutable planning dataclasses**

Implement the interfaces above using diacritic-free, case-insensitive comparison while retaining original spelling for output. Match `T`/`Tiet`, positive section number, and part 1/2 separated by underscore, hyphen, or whitespace; reject multiple conflicting matches.

- [ ] **Step 4: Write failing grouping, fallback, collision, and safety tests**

Add tests named `test_pairs_exactly_one_part_in_part_order`, `test_missing_duplicate_and_unrecognized_parts_are_standalone`, `test_normalized_case_and_diacritic_group_is_deterministic`, `test_maps_merged_and_standalone_outputs`, `test_detects_logical_output_collisions`, and `test_rejects_paths_that_escape_roots`. Assert exact physical merged path `Toan7/Bài 12/T36/T36_Slide.pptx`, exact JSON path `Toan7/Bài 12/T36/T36_Slide.json`, member order part 1 then part 2, independent `<original-stem>.json` outputs for fallback, fatal issues for duplicate logical JSON paths, and no absolute/`..` output.

- [ ] **Step 5: Run the new grouping tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_planning.py -v`

Expected: parser tests PASS; plan-building tests FAIL because grouping/output mapping is not implemented.

- [ ] **Step 6: Implement `plan_documents` and `standalone_document`**

Sort inputs deterministically, group by normalized `(course, lesson, section)`, emit one merged plan only for an exact pair, emit every member independently otherwise, and collect non-fatal warnings plus fatal output-collision issues. Set a merged document's `source_path` to its shared target even before that file exists, and set `action='reuse'` solely from target existence.

- [ ] **Step 7: Run planning tests**

Run: `.venv/bin/python -m pytest tests/test_planning.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the planner**

```bash
git add quiz_generator/planning.py tests/test_planning.py
git commit -m "feat: plan pptx part groups"
```

### Task 2: Single-pair Node PPTX Merge Worker

**Files:**
- Create: `package.json`
- Create: `package-lock.json`
- Modify: `.gitignore`
- Create: `tools/pptx-merge-worker.mjs`
- Create: `tests/pptx-merge-worker.test.mjs`

**Interfaces:**
- Produces CLI: `node tools/pptx-merge-worker.mjs --part1 PATH --part2 PATH --output PATH`.
- Produces success protocol: exactly one stdout JSON object `{"ok":true,"part1Slides":N,"part2Slides":N,"outputSlides":N}` and exit 0.
- Produces source-error protocol: exactly one stdout JSON object `{"ok":false,"kind":"source","error":"..."}` plus diagnostics on stderr and non-zero exit.
- Produces exports for tests: `extractImageSlides(buffer)`, `countSlides(fileOrBuffer)`, and `mergePair({part1, part2, output})`.

- [ ] **Step 1: Add the pinned Node manifest and a failing worker test**

Create a private ESM package with exact dependencies `jszip: 3.10.2` and `pptxgenjs: 4.0.1`, and scripts `test:node` and `test`. Add `node_modules/` to `.gitignore`. In the Node test, create tiny red/blue image-only presentations and assert the missing worker would merge part 1 before part 2 with exact slide counts and page size.

- [ ] **Step 2: Install dependencies and verify the worker test fails**

Run: `npm install`

Run: `npm run test:node`

Expected: FAIL because `tools/pptx-merge-worker.mjs` does not exist or has no exports.

- [ ] **Step 3: Port the established image extraction and merge engine**

Adapt `pyramid-upload-to-r2/scripts/merge-document-slides-node.mjs` into the single-pair interface. Preserve true OOXML slide order, page dimensions, supported PNG/JPEG/GIF/SVG images, and image `x/y/w/h`. Reject direct slide-tree content other than picture and OOXML bookkeeping nodes so unsupported text/chart/media is not silently dropped.

- [ ] **Step 4: Run the happy-path Node test**

Run: `npm run test:node`

Expected: happy-path order/count/geometry test PASS.

- [ ] **Step 5: Add failing Node error-contract tests**

Add tests for different page sizes, an empty deck, a slide without pictures, a non-picture shape, malformed OOXML, unsupported image extension, and CLI failure JSON. Each source error must set `kind` to `source`; no test may leave the requested output behind.

- [ ] **Step 6: Implement validation and exact CLI JSON output**

Keep module-load failures outside the source-error handler so missing npm dependencies remain distinguishable as infrastructure failures to Python. Ensure stdout contains only protocol JSON and all human diagnostics use stderr.

- [ ] **Step 7: Run Node tests and inspect dependency versions**

Run: `npm test`

Run: `npm ls jszip pptxgenjs`

Expected: all Node tests PASS; versions are exactly `jszip@3.10.2` and `pptxgenjs@4.0.1`.

- [ ] **Step 8: Commit the worker and dependencies**

```bash
git add package.json package-lock.json .gitignore tools/pptx-merge-worker.mjs tests/pptx-merge-worker.test.mjs
git commit -m "feat: add node pptx merge worker"
```

### Task 3: Python Worker Adapter, Atomic Materialization, and Locking

**Files:**
- Create: `quiz_generator/pptx_merge.py`
- Create: `tests/test_pptx_merge.py`
- Modify: `quiz_generator/planning.py`
- Modify: `tests/test_planning.py`

**Interfaces:**
- Consumes: `DocumentPlan`, `PlannedDocument`, and `standalone_document` from Task 1.
- Consumes: Node JSON protocol from Task 2.
- Produces: `MergeResult(part1_slides: int, part2_slides: int, output_slides: int)`.
- Produces: `SourceMergeError` and `MergeInfrastructureError`.
- Produces: `NodePptxMerger(node_binary: str = 'node', worker_path: Path = ROOT / 'tools/pptx-merge-worker.mjs', timeout: float = 300)`.
- Produces: `NodePptxMerger.merge(part1: Path, part2: Path, output: Path) -> MergeResult`.
- Produces: `PptxMaterializer(merger: NodePptxMerger)` and `PptxMaterializer.materialize(plan: DocumentPlan, on_event: Callable[[dict], None] | None = None) -> DocumentPlan`.

- [ ] **Step 1: Write failing adapter protocol tests**

Add tests named `test_adapter_returns_typed_slide_counts`, `test_source_failure_and_infrastructure_failure_are_distinct`, `test_success_with_malformed_or_extra_stdout_is_infrastructure_failure`, and `test_timeout_and_missing_node_are_infrastructure_failures`. Mock `subprocess.run` and assert an argument list, `shell=False`, timeout propagation, exact JSON parsing, and no secret or raw process body stored in errors.

- [ ] **Step 2: Run adapter tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pptx_merge.py -k 'adapter or failure or stdout or timeout' -v`

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement `NodePptxMerger` and typed errors**

Parse only one complete JSON object from stdout. Map `{kind: source}` to `SourceMergeError`; map executable/package failure, timeout, signal, missing output, inconsistent counts, or protocol corruption to `MergeInfrastructureError`.

- [ ] **Step 4: Write failing materialization and concurrency tests**

Add tests named `test_existing_target_is_reused_without_worker`, `test_source_error_falls_back_to_two_standalones`, `test_infrastructure_error_does_not_fallback`, `test_temporary_output_is_atomic_and_cleaned`, `test_resolved_output_cannot_escape_merged_root`, and `test_concurrent_materializers_create_once_and_waiter_reuses`. Assert events `merging`, `merged`, `reused`, and `merge_fallback` carry the planned state key and warning.

- [ ] **Step 5: Run materialization tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pptx_merge.py -k 'existing or fallback or temporary or escape or concurrent' -v`

Expected: FAIL because `PptxMaterializer` is not implemented.

- [ ] **Step 6: Implement lock/recheck/temp/rename materialization**

Use blocking `fcntl.flock` on `<merged-root>/.locks/<sha256(destination)>.lock`. Resolve and validate destination and temporary paths under merged root, recheck after lock acquisition, create a unique temporary `.pptx` in the destination directory, call the adapter, `os.replace` only after verified success, and always remove a leftover temporary file. On `SourceMergeError`, replace the merged plan entry with two ordered standalone entries; propagate `MergeInfrastructureError`.

- [ ] **Step 7: Run planner and materializer tests**

Run: `.venv/bin/python -m pytest tests/test_planning.py tests/test_pptx_merge.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the adapter and materializer**

```bash
git add quiz_generator/pptx_merge.py quiz_generator/planning.py tests/test_pptx_merge.py tests/test_planning.py
git commit -m "feat: materialize shared merged presentations"
```

### Task 4: Planned-document Quiz Pipeline

**Files:**
- Modify: `quiz_generator/pipeline.py:75-192`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `PlannedDocument` from Task 1.
- Adds constructor parameter: `document_provider: Callable[[], Sequence[PlannedDocument]] | None = None`.
- Preserves existing constructor parameters, including `path_mapping`, for backward-compatible standalone behavior.
- Uses `source_path` for reading/fingerprinting, `state_key` for durable state/events, `prompt_path` for the LLM source name, and the complete `output_path` for JSON persistence.

- [ ] **Step 1: Write failing external-physical/logical-path tests**

Add `test_planned_document_can_live_outside_source_root`, `test_planned_paths_drive_state_prompt_output_and_events`, and `test_provider_is_recalled_on_every_watch_pass`. Assert a merged physical PPTX outside `documents_root` writes the canonical JSON path, stores the logical state key, sends the prompt path, and a second provider result is discovered on the next `run_once`.

- [ ] **Step 2: Run focused pipeline tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k 'planned or provider' -v`

Expected: FAIL because `QuizRunner` has no `document_provider`.

- [ ] **Step 3: Refactor discovery into a planned-document provider**

When no provider is supplied, adapt current `discover_documents` and optional `path_mapping` into standalone `PlannedDocument` values so existing callers and tests retain their current `.pdf.json`/`.pptx.json` behavior. Planner-produced documents use the spec's `<original-stem>.json` or `Tn_Slide.json` paths. Cache the most recent planned state keys for `has_unresolved_failures`; do not assume a state key is a path below `documents_root`.

- [ ] **Step 4: Run all pipeline tests**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`

Expected: PASS, including existing resume, cooldown, limit, stop, and mapping tests.

- [ ] **Step 5: Commit the pipeline integration**

```bash
git add quiz_generator/pipeline.py tests/test_pipeline.py
git commit -m "refactor: process planned quiz documents"
```

### Task 5: Shared Configuration and CLI/Watch Integration

**Files:**
- Modify: `quiz_generator/config.py`
- Modify: `quiz_generator/documents.py:28-34`
- Modify: `generate_quiz.py`
- Modify: `tests/test_documents.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: planning/materialization interfaces from Tasks 1 and 3.
- Produces: `QuizPaths(documents: Path, merged_documents: Path, output: Path)`.
- Produces: `resolve_quiz_paths(env_file: Path, *, documents: Path | None = None, merged_documents: Path | None = None, output: Path | None = None) -> QuizPaths`.
- Extends: `discover_documents(root: Path, *, exclude_roots: Iterable[Path] = ()) -> list[Path]`.
- Adds CLI option: `--merged-documents PATH`.

- [ ] **Step 1: Write failing config and excluded-root tests**

Assert the three exact default paths from the spec, shell environment precedence over `.env`, CLI override precedence, and exclusion when output/merged roots are nested below the source. Include a symlinked excluded directory test so recursive discovery cannot re-ingest results through an alias.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_documents.py tests/test_cli.py -k 'config or exclude or merged' -v`

Expected: FAIL because path resolution and exclusions are absent.

- [ ] **Step 3: Implement configuration and safe discovery exclusions**

Resolve all roots before comparisons. Prune excluded directory trees rather than filtering only after a full recursive scan, and retain stable case-insensitive suffix ordering.

- [ ] **Step 4: Write failing CLI planning/watch/list tests**

Add `test_list_reports_merge_reuse_and_standalone_without_running_worker`, `test_cli_generates_one_json_for_existing_merged_pair`, and `test_watch_replans_when_second_part_arrives`. The list test must show `merge`, `reuse merged`, warnings, and totals without requiring API credentials or invoking Node.

- [ ] **Step 5: Run CLI tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`

Expected: existing tests PASS; new planning tests FAIL.

- [ ] **Step 6: Integrate planner/materializer into CLI and watch**

Build a provider that discovers current physical files, plans them, logs issues, materializes when not in `--list`, and supplies final documents to `QuizRunner` on every pass. `--list` uses pure plan data only and exits non-zero only for fatal planning conflicts. Preserve existing exit codes and `--limit` semantics over final documents.

- [ ] **Step 7: Run CLI/document tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_documents.py -v`

Expected: PASS.

- [ ] **Step 8: Commit CLI integration**

```bash
git add quiz_generator/config.py quiz_generator/documents.py generate_quiz.py tests/test_documents.py tests/test_cli.py
git commit -m "feat: merge pptx parts in cli and watch"
```

### Task 6: Web Preview and Durable Queue Integration

**Files:**
- Modify: `quiz_generator/web.py`
- Modify: `quiz_generator/jobs.py`
- Modify: `app.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `plan_documents`, `PptxMaterializer`, and planned-document `QuizRunner`.
- Extends: `preview(kind: str, paths: list[str], merged_root: Path) -> dict` with physical `files`, logical `documents`, issues, actions, and members.
- Extends: `create_app(..., merged_root: Path | None = None, merger: NodePptxMerger | None = None)` for isolated tests and deployment configuration.
- Extends durable job metadata with `sources`, logical `files`, `members`, `action`, `warning`, and statuses `merging`/`reused` while keeping result lookup by stable logical file ID.

- [ ] **Step 1: Write failing preview tests**

Add tests for a complete uploaded pair producing one `merge` document, an existing shared target producing `reuse`, duplicate/missing parts producing standalone documents plus warnings, and unsafe/colliding paths returning HTTP 400. Assert preview performs no filesystem write.

- [ ] **Step 2: Run preview tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -k 'preview and (merge or reuse or part or unsafe)' -v`

Expected: FAIL because preview returns only physical files.

- [ ] **Step 3: Implement planned preview and shared root injection**

Keep `preview.files` as the exact physical upload list used by `FormData`; add `preview.documents` for UI. Resolve the default merged root through `resolve_quiz_paths(env_file)` and pass it into `JobManager` from `create_app`/`app.py`.

- [ ] **Step 4: Write failing job lifecycle tests**

Add `test_pair_job_materializes_once_and_outputs_one_logical_json`, `test_existing_merge_skips_worker`, `test_source_merge_failure_becomes_two_standalone_rows`, `test_infrastructure_merge_failure_fails_job`, `test_zip_contains_only_final_logical_json_tree`, and `test_restart_resets_merging_row_to_pending_and_marks_interrupted`. Assert merged output ends in `T36_Slide.json` and standalone output strips `.pdf`/`.pptx` before `.json`. Use injected fake merger/materializer behavior; do not call a real LLM or Node process in backend unit tests.

- [ ] **Step 5: Run job tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -k 'pair_job or existing_merge or merge_failure or logical_json or restart' -v`

Expected: FAIL because jobs still model one row per uploaded file.

- [ ] **Step 6: Refactor `JobManager` around physical sources and logical documents**

Persist uploaded source mapping separately from final logical rows. Build the authoritative plan from staged paths, publish `merging`/`reused`/fallback events, reconcile a failed merged row into two stable standalone rows, and pass the final sequence through `QuizRunner.document_provider`. Reset `merging`, `reading`, and `generating` rows to `pending` on restart. Keep FIFO dispatch, stop behavior, API-key redaction, result lookup, and ZIP generation intact.

- [ ] **Step 7: Run all web backend tests**

Run: `.venv/bin/python -m pytest tests/test_web.py -v`

Expected: PASS, including existing unlimited-upload, cancellation, FIFO, restart, Unicode, and download tests.

- [ ] **Step 8: Commit web backend integration**

```bash
git add quiz_generator/web.py quiz_generator/jobs.py app.py tests/test_web.py
git commit -m "feat: add pptx merge planning to web jobs"
```

### Task 7: Website Merge Preview and Status Presentation

**Files:**
- Modify: `static/app.js`
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `preview.documents[*].action`, `members`, and `warning` from Task 6.
- Consumes: job row statuses `merging`, `reused`, `reading`, `generating`, `done`, `skipped`, `failed`.
- Preserves: upload `FormData` from `preview.files`, not from grouped logical documents.

- [ ] **Step 1: Write a failing browser test for merge preview and lifecycle**

Add `test_browser_shows_one_merged_document_with_members_and_status` using an injected backend fake merger. Assert two selected part files render one logical preview card labeled `Sẽ ghép`, show both member names, submit both physical files, display `Đang ghép` or `Dùng bản đã ghép`, and finish with one downloadable JSON row.

- [ ] **Step 2: Run the focused browser test and verify it fails**

Run: `QUIZ_UI_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_ui.py -k merged_document -v`

Expected: FAIL because the UI has no grouped plan display or merge labels.

- [ ] **Step 3: Implement the grouped plan summary and status labels**

Add a compact logical-document list beneath the source tree, member sublabels, warning styling, and labels for `merge`, `reuse`, `standalone`, `merging`, and `reused`. Include `merging` in the active-row/progress logic. Keep all uploaded names assigned through `textContent` and preserve responsive/mobile layout.

- [ ] **Step 4: Run all browser tests**

Run: `QUIZ_UI_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_ui.py -v`

Expected: PASS with no page errors and no horizontal overflow on mobile.

- [ ] **Step 5: Commit frontend changes**

```bash
git add static/app.js static/index.html static/styles.css tests/test_ui.py
git commit -m "feat: show pptx merge plans in quiz studio"
```

### Task 8: Ubuntu Setup, Configuration Examples, Documentation, and Final Verification

**Files:**
- Create: `setup-ubuntu.sh`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: exact Python/Node dependency requirements and CLI/web behavior from Tasks 1–7.
- Produces: `setup-ubuntu.sh` that checks Python 3.10+, checks Node 20+, creates/reuses `.venv`, installs `requirements.txt`, and runs `npm ci` without modifying `.env`.

- [ ] **Step 1: Write failing setup/config documentation checks**

Add a CLI test that invokes `bash -n setup-ubuntu.sh`, and assertions that `.env.example` documents `QUIZ_DOCUMENTS_DIR`, `QUIZ_MERGED_DOCUMENTS_DIR`, and `QUIZ_OUTPUT_DIR` without exposing secrets. Preserve all pre-existing `.env.example` lines exactly except for the appended path section.

- [ ] **Step 2: Run the checks and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k 'setup or env_example' -v`

Expected: FAIL because the setup script and path examples are absent.

- [ ] **Step 3: Add the Ubuntu setup script and update documentation**

Document Node 20+, `npm ci`, shared merged-file immutability, class/lesson/section output examples, CLI/watch automatic behavior, website preview/status/fallback behavior, and how to intentionally rebuild by manually moving/removing an existing merged PPTX. Do not make application startup run package installation.

- [ ] **Step 4: Run syntax and focused setup checks**

Run: `bash -n setup-ubuntu.sh`

Run: `.venv/bin/python -m pytest tests/test_cli.py -k 'setup or env_example' -v`

Expected: PASS.

- [ ] **Step 5: Run the complete automated suite**

Run: `npm test`

Run: `.venv/bin/python -m pytest -q`

Run: `QUIZ_UI_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_ui.py -q`

Expected: Node, Python, and browser suites all PASS; no test calls Ollama Cloud or uses a real API key.

- [ ] **Step 6: Perform a real read-only planning smoke check on the current source tree**

Run: `.venv/bin/python generate_quiz.py --documents /home/meconlonton/Documents/quiz/documents --merged-documents /home/meconlonton/Documents/quiz/merged-documents --output /home/meconlonton/Documents/quiz/documents/output --list`

Expected: exit 0 unless true logical-output collisions are reported; output lists merge/reuse/standalone actions and creates no PPTX or JSON.

- [ ] **Step 7: Confirm repository cleanliness and review scope**

Run: `git status --short`

Expected: only the user's pre-existing `.env.example` work remains outside this task's staged changes, or it has been preserved as part of the intentionally appended configuration section; no `.env`, `node_modules`, generated PPTX, JSON output, or job workspace is staged.

- [ ] **Step 8: Commit setup and documentation**

```bash
git add setup-ubuntu.sh README.md tests/test_cli.py
git add -p .env.example  # stage only the appended QUIZ_* path hunk; preserve pre-existing user edits unstaged
git commit -m "docs: add ubuntu pptx merge setup"
```

- [ ] **Step 9: Record final verification evidence**

Run: `git log --oneline --max-count=10`

Run: `git status --short --branch`

Expected: implementation commits are present in task order and the branch contains no unintended generated or secret files.
