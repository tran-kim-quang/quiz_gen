# Execution ledger: 2026-10-02-quiz-ui.md

- User authorized UI implementation; proceed inline. No Git repository or worktree/commit actions.
- Task 1 complete: input/path mapping and pipeline progress/stop tests first failed, then 55 tests passed.
- Task 2 complete: new API tests failed before implementation, then backend endpoints and worker passed; shared config extracted without changing CLI semantics.
- Task 3 complete: browser test failed against absent UI; implemented HTML/CSS/JS. Real Chromium folder ZIP, single JSON, quiz modal, reload and 390px mobile checks passed. Desktop/mobile screenshots visually inspected.
- Browser test adjustment: Chromium sanitizes nonportable `<`/`>` in native download filenames. Keep HTML-special-name escaping and exact original ZIP-name checks in the folder test; use a portable accented basename to verify exact native JSON downloads.
- Independent review found output file/directory and reserved metadata-name collisions. Added three failing API regression cases, rejected impossible output paths during preview/upload and mapped pipeline initialization before generation.
- Task 4 complete: README updated, full suite including Chromium passed 72 tests; JS syntax and Python compile checks passed.
- Live UI smoke complete: browser uploaded an accented folder tree containing the explicitly selected Toán 7 reference; configured cloud model returned a 10-question quiz with title `Quiz Toán 7 - Bài 12: Tiết 36 - Tổng các góc trong một tam giác`. Downloaded ZIP verified exact path `Lớp 7/Môn/Toán 7/Bài 12/Tiết 36/Tài liệu.pptx.json`.
- Discarded first disposable smoke job: an added Ngữ văn reference was selected by an unordered iterator and staged under a Toán example tree. Removed only that job's owned metadata/output, selected the Toán source explicitly and reran successfully. Original documents remained untouched.
- Live successful job: `007f43cce9ef46da929899b7cafa624e`. UI remains running at `http://127.0.0.1:8000`; HTTP 200 and completed 1/1 result verified.
- Dependency bound: Starlette <1 retains compatibility with the existing httpx test/client usage; remaining test deprecation warning is third-party anyio portal typing.
