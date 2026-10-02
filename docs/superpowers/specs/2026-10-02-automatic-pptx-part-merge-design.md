# Automatic PPTX Part Merge Design

## Status

Approved conversational design, ready for written-spec review.

## Context

`quiz_gen` currently discovers PDF/PPTX inputs and generates one quiz JSON per
input document. The command-line, watch, and web job paths all eventually use
the same quiz-generation pipeline, but there is no planning stage that can
turn two physical PPTX part files into one logical lesson document.

The source collection under `/home/meconlonton/Documents/quiz/documents` uses
more than one directory layout. Most files have a redundant course wrapper,
for example `Toan7/Toan7 pptx/Bai .../file.pptx`, while other courses already
have explicit lesson directories. PPTX part names also have both short and
prefixed forms, such as `T35_2.pptx` and
`TOAN7_C4_B12_T36_1_fixed.pptx`.

The existing `pyramid-upload-to-r2` repository contains a proven image-only
PPTX merge engine built with Node.js, `jszip`, and `pptxgenjs`. Its current CLI
cannot be called unchanged because it requires exactly `Course/Bai/Tiet`,
scans a whole directory, aborts all work when one group is irregular, and
treats an existing output as an error. This design reuses that engine inside a
single-pair worker while keeping discovery and queue behavior in `quiz_gen`.

## Goals

- Make automatic part discovery the default for CLI, watch, and web jobs.
- Merge exactly one part 1 and one part 2 for the same class, lesson, and
  section, always in part 1 then part 2 order.
- Treat missing, duplicate, or unrecognized parts as independent documents.
- Store merged PPTX files in one shared persistent tree organized by
  class/lesson/section.
- Store generated JSON under the configured output root using the same logical
  class/lesson/section organization.
- Reuse an existing merged PPTX without comparing it with source files or
  rebuilding it.
- Preserve the existing FIFO web queue and expose planning/merge status in the
  website.
- Keep an irregular source file from preventing unrelated documents from
  being processed.

## Non-goals

- Detecting whether source parts changed after a merged PPTX was created.
- Overwriting, refreshing, or deleting an existing merged PPTX.
- Supporting editable text, charts, media, SmartArt, or arbitrary PowerPoint
  shapes in the merge worker. The current collection is image-only.
- Calling the old repository at runtime or requiring the two repositories to
  be deployed side by side.
- Changing quiz generation prompts, answer parsing, or provider behavior.

## Selected Architecture

Add a document-planning layer before `QuizRunner`. The planner receives
physical input files and returns logical documents. A logical document has:

- a physical source path used by the extractor;
- a logical relative path used for output naming and state;
- a kind: `merged` or `standalone`;
- zero or more source-member paths for display and diagnostics;
- planning warnings, if any.

The same planner is used by CLI scans, watch scans, web preview, and web job
execution. Web preview is advisory because it only has client paths; the job
reruns planning after uploads have been safely staged.

For a valid pair, the planner materializes or reuses one PPTX beneath the
shared merged root and emits only that logical document. It does not also emit
the two part files. For every other case, it emits each physical file as its
own standalone logical document.

`QuizRunner` is extended to accept planned documents so physical storage and
logical output paths no longer need to share a root. Existing direct-file
behavior remains available through standalone plans.

## Configuration

The default deployment uses:

```env
QUIZ_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/documents
QUIZ_MERGED_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/merged-documents
QUIZ_OUTPUT_DIR=/home/meconlonton/Documents/quiz/documents/output
```

CLI flags override environment values, including a new merged-document root
option. Configuration resolution must produce absolute paths before scanning.
The output root and merged root are explicitly excluded if either is inside
the source tree.

The Node executable defaults to `node` and may be overridden for deployment,
but the worker script itself is shipped inside `quiz_gen`.

## Path and Part Recognition

Matching is case-insensitive and removes Vietnamese diacritics for comparison
only. Original directory spelling is retained for output paths.

### Class and lesson

- The class is the first meaningful course segment, such as `Toan7`.
- A redundant wrapper such as `Toan7 pptx` or `Toan 8 pptx` is omitted from
  the logical path.
- The lesson is the source segment representing `Bai ...`.
- Existing explicit section directories such as `Tiet 36 ...` are recognized
  and retained for validation, but canonical merged output uses `T36`.

If class or lesson cannot be identified safely, the file is not guessed into
a group. It is processed independently using its safe source-relative path.

### Section and part

The filename parser finds a `T` or `Tiet` token followed by a positive section
number and then part `1` or `2`, with underscore, hyphen, or whitespace
separators. It therefore recognizes both:

```text
TOAN7_C4_B12_T36_1_fixed.pptx  -> section 36, part 1
T35_2.pptx                     -> section 35, part 2
```

The parser rejects names with no match or multiple conflicting matches. When
an explicit section directory exists, its section number must agree with the
filename; otherwise that file is standalone with a warning.

### Grouping

Recognized part files are grouped by normalized `(class, lesson, section)`.
A group is mergeable only when it contains exactly one part 1, exactly one
part 2, and no additional PPTX files claiming either part. Otherwise every
file in that group is processed independently. PDFs and PPTX files that do not
claim a part are always standalone.

## Output Mapping

A valid pair for class `Toan7`, lesson `Bai 12`, and section 36 maps to:

```text
<merged-root>/Toan7/Bai 12/T36/T36_Slide.pptx
<output-root>/Toan7/Bai 12/T36/T36_Slide.json
```

A standalone file with a recognized section maps to:

```text
<output-root>/Toan7/Bai 12/T36/<original-stem>.json
```

When the section cannot be recognized, its safe source-relative directory and
original stem are mirrored beneath the output root. This avoids inventing a
wrong section.

If two planned documents resolve to the same logical JSON path, both are
marked as planning errors and neither may overwrite the other. Other
non-conflicting documents continue. CLI execution returns a non-zero status if
any such error occurs; a web job records the affected entries as failed.

## Shared Merged-File Semantics

The merged tree is shared by CLI, watch, and every web job. An existing target
PPTX is authoritative and immutable for this workflow:

- its source timestamps, sizes, and hashes are not compared;
- it is not opened or validated during planning;
- it is used directly as the physical quiz source;
- any later extraction failure is reported normally and does not trigger an
  automatic rebuild.

This intentionally means that a later web upload with the same logical
class/lesson/section uses the earlier merged file. Rebuilding requires a user
to remove or relocate the existing result outside this workflow.

Creation is serialized by an advisory file lock derived from the destination
path and stored under a lock directory in the merged root. After acquiring
the lock, the process checks for the destination again. This supports
concurrent CLI and website processes on the same Ubuntu host. It does not
claim distributed locking across unrelated hosts or filesystems.

## Node Merge Worker

The image-only merge engine from `pyramid-upload-to-r2` is brought into
`quiz_gen` as a self-contained Node worker. It continues to use `jszip` to
read PPTX packages and `pptxgenjs` to create the result. The worker accepts one
pair rather than scanning directories:

```text
node <worker> --part1 <path> --part2 <path> --output <temporary-path>
```

Arguments are passed as a subprocess argument array, never through a shell.
The worker writes one JSON result to stdout and diagnostics to stderr. Its
success result includes slide counts for part 1, part 2, and output.

For each source presentation, the worker:

1. Reads the true presentation slide order from OOXML relationships.
2. Reads slide dimensions.
3. Extracts every supported picture with its coordinates and dimensions.
4. Rejects a slide with no image, an unsupported image type, missing
   relationships, or unreadable OOXML.
5. Requires both presentations to use the same page dimensions.
6. Adds every part 1 slide followed by every part 2 slide.
7. Reopens the generated package and verifies that output slide count equals
   the sum of both inputs.

The worker writes only to a unique temporary path in the destination
directory. Python validates the successful response and atomically renames the
temporary file while still holding the lock. Any temporary artifact is
removed after failure.

Source-specific merge failures, such as a malformed presentation or an
unsupported shape layout, produce a warning and cause both source parts to be
planned as standalone documents. Infrastructure failures, such as a missing
Node executable, missing npm dependency, timeout, or malformed worker
protocol, are configuration errors and fail the affected execution rather
than silently disabling automatic merging.

## CLI and Watch Flow

Each CLI scan performs these stages:

1. Discover supported input files while excluding configured result roots.
2. Build and validate the document plan.
3. Materialize or reuse merged PPTX files.
4. Hand final logical documents to `QuizRunner`.
5. Persist quiz JSON and existing processing state against logical paths.

The listing/dry-run view reports `merge`, `reuse merged`, `standalone`,
warning, and conflict outcomes without generating quiz JSON. A dry run does
not invoke the merge worker.

Watch mode reruns discovery and planning whenever its current polling cycle
detects filesystem changes. Newly completed pairs therefore become one
logical document on the next cycle. Once a merged output exists, later source
changes do not invalidate it. Fingerprinting and skip behavior use the final
physical document, so a reused merged file remains stable.

## Web Flow

### Preview

The preview endpoint normalizes and validates browser-provided relative paths,
then applies the same recognition/grouping rules without opening PPTX content.
The UI displays:

- one row labeled `Se ghep` for a valid pair, with both member names;
- one row per standalone document;
- warnings for missing, duplicate, conflicting, or unrecognized parts;
- `Dung ban da ghep` when the shared target already exists.

Preview never creates a merged file and is not authoritative.

### Job execution

All uploaded physical files remain isolated under the existing job input
directory. After staging, the job rebuilds the plan, materializes or reuses
shared merged files, and sends only final logical documents to `QuizRunner`.

The existing FIFO queue remains unchanged. Per-document status progresses
through the applicable states:

```text
waiting -> merging -> generating quiz -> completed
```

A reused file skips `merging` and records that it used an existing merged
PPTX. A recoverable merge failure creates two standalone rows, attaches a
visible warning, and continues. Stop requests are honored between final
documents, not while the Node worker is writing a temporary PPTX.

The result ZIP contains only final quiz JSON files and preserves their logical
class/lesson/section hierarchy. It does not contain uploaded parts or merged
PPTX files.

### Path safety

Existing upload normalization remains mandatory. Logical path construction
must reject absolute paths, `..`, empty path components, and any resolved path
outside the configured roots. File names are supplied to the worker as
arguments and are never interpolated into shell commands.

## Error Handling and Observability

- Irregular input naming is a document warning, not a global abort.
- Duplicate parts cause every member of that group to run independently.
- Source-specific merge failure falls back to independent processing and
  preserves the worker diagnostic in CLI logs and web metadata.
- Infrastructure failure is explicit and actionable, including the missing
  executable or package name when available.
- Output collisions never overwrite data.
- Existing merged outputs are never replaced by this feature.
- Temporary files are cleaned up on worker failure, cancellation between
  documents, and normal process cleanup where possible.
- Logs include the logical key, both member paths, chosen destination, whether
  the destination was created or reused, and slide counts on success.

## Dependencies and Setup

`quiz_gen` gains a minimal Node package manifest containing pinned compatible
versions of `jszip` and `pptxgenjs`. Ubuntu setup documentation and scripts
install both Python and npm dependencies and require Node.js 20 or newer.
Application startup may report dependency readiness, but it must not mutate
the environment by running `npm install` automatically.

The Python project does not add `python-pptx`; PowerPoint merging remains the
responsibility of the Node worker.

## Testing Strategy

### Python unit tests

- Parse short, prefixed, case-varied, and diacritic filename forms.
- Reject ambiguous or mismatched section identities.
- Normalize redundant course wrappers.
- Build a valid pair, incomplete pair, duplicate-part group, and standalone
  PDF/PPTX plan.
- Map merged and standalone logical paths correctly.
- Detect output collisions and prevent path traversal.
- Reuse an existing merged target without invoking the worker.
- Serialize concurrent creation and recheck the destination after locking.
- Distinguish recoverable source failures from infrastructure failures.

### Node tests

- Preserve part 1 then part 2 slide order.
- Preserve source page size and image placement supported by the old engine.
- Reject page-size mismatch, empty presentations, missing images, unsupported
  media, and malformed packages.
- Verify output slide count.
- Emit the documented JSON protocol and non-zero exit status on failure.

### Pipeline and web integration tests

- Feed planned documents whose physical and logical paths differ into
  `QuizRunner`.
- Confirm CLI/watch exclude output and merged roots.
- Confirm preview groups a pair and exposes warnings.
- Confirm job status includes merge, reuse, fallback, and generation stages.
- Confirm a fallback pair produces two independent JSON outputs.
- Confirm result ZIP hierarchy contains only final JSON documents.
- Confirm concurrent jobs targeting one shared merged path create or reuse a
  single complete PPTX.

## Acceptance Criteria

- A complete two-part lesson is represented by one merged PPTX and one quiz
  JSON in class/lesson/section paths from CLI, watch, and website.
- A missing, duplicate, or unrecognized part is processed independently and
  does not block unrelated inputs.
- Existing merged PPTX files are reused unchanged.
- Website preview and job status clearly communicate merge, reuse, fallback,
  and error outcomes.
- No workflow writes outside configured roots or exposes shell interpolation.
- The Node worker retains the behavior of the established image-only engine
  and verifies the final slide count.
- Automated tests cover planning, merging, shared-file concurrency, pipeline
  mapping, and web integration.
