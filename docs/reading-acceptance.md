# Reading workflow: scope and acceptance

## Agreed product behavior

This is a general-purpose open-source research tool. Research profiles belong to the user; the motor-imagery EEG example is a validation scenario, not a default taxonomy or scientific assumption.

| Requirement | Implementation / acceptance |
| --- | --- |
| Zotero entry points | Exact current selection via a separately installed, bundled Zotero companion; existing personal-library collection/tag/query/key selectors remain available. Multiple PDFs require an explicit attachment choice. |
| Per-task choices | The skill asks for outputs, quick/deep mode, explanation depth, layout, annotation detail, priority and execution mode; validated preferences can be saved. |
| Current DSH model | Python prepares and validates data. The host model produces translation and analysis. No new model endpoint or credential is configured. Parallel work is an opt-in instruction to use available DSH internal children inheriting current configuration; economy is default. |
| Local setup | A pinned, checksum-verified uv archive prepares a private Python environment; PDF, OCR and embedded Noto font components are fetched on demand with progress. No install-time script runs on npm installation. |
| Full translation | Translation coverage is checked for text blocks in abstract, main text and substantive appendices. References, captions, tables and detected equations retain their source representation unless separately translated. Unreadable pages block complete export. |
| Reading quality | Claims must identify their category and whether they are reported, interpreted or proposed; exact substantial quotes must resolve to source blocks and geometry. Scientific correctness still requires model and human review. |
| PDF output | Annotated copy with Chinese comments; side-by-side, stacked or alternating bilingual editions. Original pages remain intact; text overflow continues on additional pages. Raster figures, detected tables and equations are copied to translated pages. Original pages are authoritative for complex vector diagrams. |
| Editable documents | Markdown notes, translations and reviews. Zotero note publication is explicit and idempotent for an unchanged accepted analysis. Outputs get new revision directories and do not overwrite edited files. |
| Multi-paper reuse | Synthesis consumes accepted single-paper claims and source links. Missing evidence produces targeted queries and automatically retrieved, reasoned candidates with metadata/abstract evidence labels. Candidates are not silently imported or treated as full-text findings. |
| Recovery | PDF extraction is content-addressed. Accepted chunks are immutable and serialized by a job lock; status identifies untranslated blocks, unexamined categories and unreadable pages. |

## Verified in the development container

- 90 Python tests: 44 original, 17 initial workflow tests, and 29 lightweight enhancement tests. They exercise evidence rejection, coverage, parse/model-result reuse, correction history and stale-context rejection, scoped retrieval and FTS fallback, matrix evidence/CSV handling, native annotation geometry/idempotence/user edits, terminology/numeric review, HTTP header casing, plus the original PDF/cache/selection contracts.
- 7 Node tests: existing skill provider plus component allowlisting, pinned runtime selection, checksum failure cleanup and companion endpoint lifecycle/authentication inheritance.
- A new private runtime downloaded and prepared PDF packages, an embedded regular-weight Noto font, and OCR packages. A synthetic raster PDF was actually OCRed and its expected text checked. Unit tests remain offline; OCR is mocked there.
- Three bilingual layouts and a highlighted PDF were generated; the side-by-side edition and highlight position were rendered and visually inspected. Chinese text, original figures and a table were readable. Fixtures are synthetic, not demonstrations of model translation quality.
- npm package preview contains the runner, reader modules, companion sources, skill references and font license, without caches, PDFs, model files, virtual environments or test artifacts.
- Bounded citation-discovery request shapes were checked against actual OpenAlex DOI resolution, batched references and citing-work queries. This is an API smoke test, not evidence of complete literature coverage.

## Lightweight enhancement boundary

The market comparison and inclusion decisions are recorded in [reader-market-choices.md](reader-market-choices.md). The second round adds no third-party dependencies or always-on processes. SQLite retrieval is lexical; absent FTS5 it falls back to a scoped standard-library scan. Source quotes, result revision and scope are validated before rendering a focused answer, matrix or synthesis. These checks do not prove scientific correctness.

Zotero-native annotations use the documented item JSON fields and existing local-API authorization. Offline tests cover rotated-page coordinates, collisions, object-level write failures and preservation of edited comments. Actual Zotero creation and reader navigation still need the on-device integration checks below. Unusual cropped/nonzero-origin PDF geometry is explicitly rejected for native writes; annotated-copy export remains available.

## Required before marking the PR production-ready

1. On a clean Windows machine, install the branch into DSH and test first-use downloads under ordinary user permissions, including paths containing Chinese characters and spaces.
2. Install the generated XPI in the actual supported Zotero version. Verify selection, parent attachment resolution, rejection without authorization, permission renewal, and removal on extension disable. The test double proves inheritance and lifecycle, not compatibility with every Zotero build.
3. Run real DSH model tasks in economy and supported internal-parallel modes. Verify the configured model is inherited, concurrency stays bounded, task choices work in the actual client, and a cancelled/interrupted task can resume.
4. Use at least one 10-15 page selectable-text paper, one 20-50 page review and one complex/scanned PDF. Check reading order, equations, diagram interpretation, quote locations and all requested output formats. Add representative failure cases to the offline test suite.
5. Evaluate translation completeness, terminology, formula explanations, experimental protocols and implementation guidance against the source. Quote matching only verifies anchoring, not reasoning validity. Verify reviews distinguish corpus limitations and hypotheses from demonstrated findings.

## Timing protocol

Record first-use component setup separately from warm-run work. Start timing after the user has selected outputs and the host begins processing. `T_first` ends at the first substantive, source-grounded card, note section or translation; a progress message does not count. `T_full` ends only when every chosen output passes coverage and export checks.

The 10-15 page target is `T_first <= 60s` and `T_full` around 180-300s. This has not been established end to end. Log model/version, mode, explanation depth, selected outputs, page/character counts, OCR pages, cache hits and actual elapsed time. Long/scanned papers require a separately measured estimate; never silently omit content to meet a deadline.
