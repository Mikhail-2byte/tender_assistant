# Repository Guidelines

## Project Structure & Module Organization

Windows desktop application using Python, PySide6, and SQLite. Keep it offline-capable; introduce servers, ORMs, or plugins only when explicitly required.

- `tender_app/`: application code. `execution_main.py` starts the current application; `execution_window.py` combines tenders, execution and the agent. `repository.py` / `execution_repository.py` own SQLite and backups. `ai_runtime.py` coordinates model requests and confirmed proposals.
- `tests/`: pytest tests and shared fixtures in `conftest.py`.
- `scripts/`: portable build, packaging, and verification tools; commands in `scripts/README.md`.
- `docs/`: implementation plan, results, trial journal, and structure guide; historical instructions in `docs/archive/`.
- `data/sample/`: fictional test database and SQL schema; `data/source/`: original Excel.
- `artifacts/`, `build/`, and `dist/`: ignored local outputs. `artifacts/execution-preview-data/` and launched applications in `dist/` contain user data. New verification outputs belong in `artifacts/checks/`.

## Build, Test, and Development Commands

Run from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run_execution.py
.\.venv\Scripts\python.exe run_execution.py --data-dir artifacts/agent-trial
.\.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1
.\.venv\Scripts\python.exe scripts/verify_portable.py
```

The last two commands build and verify the single current application, named TenderAssistantExecutionPreview for compatibility. Verification requires its ZIP. Close the executable before rebuilding. Legacy root launchers all delegate to execution_main; do not revive separate versions. Python data stays in artifacts/execution-preview-data; portable data stays beside its executable. Historical releases and their data are preserved in artifacts/legacy-releases.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and modules, `PascalCase` classes, and `UPPER_CASE` constants. Follow nearby code; no formatter or linter is configured. Use UTF-8, `pathlib`, package-qualified imports, and parameterized SQL. Preserve Russian UI wording and Moscow-time deadline semantics. Resolve application resources relative to files, not the current working directory.

## Testing Guidelines

Name tests `tests/test_*.py` and functions `test_*`. Use `tmp_path` and fixture-created database copies; never test writes against the seed or user databases. Qt tests use offscreen rendering through `conftest.py`. No numeric coverage threshold is configured. Add regression tests for behavior changes; run the full suite before handoff. Verify portable packaging after resource-path changes and inspect screenshots after layout changes.

## Commit & Pull Request Guidelines

History uses English Conventional Commit subjects: `feat: add local chat search`, `style: tighten spacing`. Use matching prefixes such as `fix:`, `refactor:`, or `docs:`. PRs should describe the problem, resulting behavior, checks, and limitations. Link relevant issues and attach screenshots for UI changes.

## Data Safety & Handoff

For AI model selection, provider limits, request budgets, or benchmark work, use explicit user requirements and provider documentation. Local profiles, raw reports and credentials are not tracked in Git.

Preserve `tender_app/data`, `artifacts/execution-preview-data`, portable `data`, documents, `.ui-backups`, and backups. Never publish source Excel or tender data; keep new source files under ignored `data/source/`. Empty historical sample folders are not tracked by Git; current portable verification expects four fictional folders, independent of the 237 historical placeholders. Record substantial results and identify the next unfinished step. The current next step is user acceptance of search, imports and platform downloads. Preserve unrelated working-tree changes and state assumptions explicitly.
