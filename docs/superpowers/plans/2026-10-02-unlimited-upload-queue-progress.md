# Execution ledger: unlimited upload queue

- User requested removing upload limits and sequential queue processing.
- Root cause: web API enforced 500 files/300 MB and `JobManager.reserve()` returned 409 while a job was active; UI disabled all upload controls whenever any active job existed.
- RED: three tests failed with second upload HTTP 409, missing queued job id, and `max_files == 500`.
- GREEN: one persistent FIFO worker now accepts multiple queued jobs; queued cancellation skips LLM; default API limits are open. Targeted queue tests passed.
- UI now stays uploadable while queue is active, shows queue position and offers queued cancellation. Selection clears after enqueue to prevent accidental duplicate submissions.
- Verification complete: 501-file multipart upload passed; the full suite including real Chromium checks passed 74 tests.
