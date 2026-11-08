# Local document reading and one Excel register

**Online models are now supported and local inference is optional.** For testing
on a weaker computer, start with [Online AI setup](ONLINE_AI.md). The local
setup below applies only when **Local — Ollama** is selected. Both providers use
the same source checks, review screen and Excel workbook.

The desktop CRM can read registered customer invoices, receipts, contracts and
other documents with a small, locally running vision-language model. It writes
one consolidated `.xlsx` workbook across all customers, with links by file ID,
source path, content hash and page/section number.

## Use it

1. Update desktop dependencies if needed:

   ```powershell
   .venv/Scripts/python.exe -m pip install -r requirements.txt
   ```

2. Open **Start Document Model.cmd**. If an Ollama service is already running
   on `127.0.0.1:11434`, the launcher uses it. Otherwise it starts an isolated
   Ollama Docker container using Docker Desktop. The first run downloads
   `qwen3-vl:2b-instruct` (about 1.9 GB). The Docker runtime is restricted to
   localhost and configured for up to 6 GB RAM and four CPUs; CPU extraction can
   take minutes per page. Start Docker Desktop first if using this route.
3. Open **Open CRM.cmd** and choose **Files → Excel register (all customers)**.
   Add originals through **Stored files** first; existing registered files and
   stored email attachments are included automatically in the register scope.
4. Choose the workbook location, or keep `exports/crm_documents.xlsx`. Click
   **Read files and update Excel**. Model errors, unsupported files and unreadable
   sources remain visible. Unchanged extractions are reused.
5. Select an extraction and choose **Review selected extraction**. Check it
   against the original, correct field/fact/line-item values and section summaries,
   add missing entries or remove incorrect ones, then save corrections or mark
   it reviewed. The workbook is refreshed after a saved review.
6. Choose **Open Excel workbook**. Close it in Excel before another update.
   **Export saved results** refreshes the workbook without calling the model.

**Automatically read new and changed files** is opt-in. After storing a file
or completing an inbox import/automation run, the CRM scans registered files,
processes changed/new content and refreshes the saved workbook. It runs only
while the desktop is open. This is event-driven, not a watcher for arbitrary
folders: use **Read files and update Excel** after editing originals externally.

With a native Ollama installation, the equivalent model setup is:

```powershell
ollama pull qwen3-vl:2b-instruct
```

The launcher-created container is named `mini-crm-document-ollama`, with model
data in Docker volume `mini_crm_document_models`. It can be stopped when unused:

```powershell
docker stop mini-crm-document-ollama
```

Run the launcher to start it again; the downloaded model is retained. No cloud
account or API key is required. The normal CRM does not download models itself.

## What is in the workbook

| Sheet | Content |
| --- | --- |
| Documents | One row per stored file, customer, source, status, warnings, summary, invoice totals and common contract fields |
| Fields | Every extracted field and value, with page/section and model-supplied source quotation |
| Line items | Invoice/receipt descriptions, quantities, unit prices, tax rates and row totals |
| Facts | Other important data such as obligations, renewal conditions, deadlines, identifiers and contacts |
| Sections | Per-page/section titles and summaries, including full summaries too long for the register |
| Readme | Scope, generation time, review meaning, limits and update behavior |

Invoice fields include supplier, supplier tax ID, buyer, invoice number, issue
date, due date, currency, subtotal, tax and total. Contract fields include
contract number, parties, effective/expiry/renewal dates, notice period and
payment terms. Other facts can be retained even when they do not fit these
standard fields.

When the model puts an explicitly named standard field in Facts instead of
Fields (for example, “Expiry Date”), a deterministic mapping copies its exact
value and quotation into the standard column as well. Generic ambiguous labels
such as “Date” are not mapped. The original fact remains available.
Manual field corrections take precedence on later exports; automatic mapping
does not recreate fields you removed during review. If a field and its original
fact disagree, a warning asks you to review both entries. Edit or remove the
incorrect Fact entry as well when correcting duplicated information.

Line-item tax rates use percentage points (20 means 20%). Invoice totals and
valid item numbers are numeric Excel cells; identifiers and source quotations
remain text, including identifiers with leading zeroes.

All model output begins as **pending review**. The model can omit or misread
important information; it cannot guarantee a complete register. Quotations are
model output, not independent proof. Missing invoice fields, conflicting page
values, malformed dates/numbers and inconsistent totals are flagged. Conflicting
values are preserved instead of silently choosing one. Multi-invoice files
should be checked in Fields/Line items rather than treated as one invoice total.

The workbook is generated from SQLite. Updates replace its sheets; changes
made only in Excel are not imported back. Save corrections inside the CRM, or
keep a separate copy of a workbook you want to edit manually. The exporter
refuses to overwrite an unrelated existing workbook. Existing CRM workbooks are
updated by writing a temporary file and replacing the old one only on success.

## Formats, source checks and privacy

Supported formats: PDF (including scans), PNG/JPEG/TIFF/BMP/WebP, DOCX, XLSX,
and UTF-8/UTF-16 text, Markdown, CSV and JSON. DOCX tables contribute text; DOCX
embedded pictures are not read. XLSX formulas are treated as text and are never
calculated. Old binary `.doc`/`.xls`, password-protected files and other formats
need conversion first. New files must be stored through the CRM; it does not
scan your whole computer or read `.env`/credentials.

Limits: 20 MB per file; 20 PDF pages/image frames; 200,000 text characters;
bounded Office ZIP expansion, spreadsheet dimensions and model response sizes.
Very dense PDF pages above 10,000 text characters require splitting/simplifying.
Page images are downscaled to a maximum of 1000 pixels per side for the small
CPU-friendly model, so small print may need a clearer source or a larger model.
PDF/image sections are pages/frames. Office/text sections are character chunks,
not printed pages. DOCX tracked changes/comments/headers and embedded objects
are not a substitute for checking the full original document.

Content hashes and model digests identify cached results. Identical files reuse
inference but keep separate file/customer rows; review status is not copied to
a new source record. A changed or missing source cannot export stale extracted
values. Stopping during a document leaves no partial result for that document;
results already saved for earlier files remain available for export. Stopping
may wait for initial vision processing; once generation streams back, the
worker checks stop requests between chunks. CPU requests have a 15-minute
timeout. Generation progress is shown without exposing document text in logs,
and the client stops reading as soon as a complete validated result is available.

Requests go only to `http://127.0.0.1:11434`, without environment proxies,
redirects, remote-model tags or tools. The model must already be downloaded and
advertise vision support. Document contents are treated as untrusted extraction
input, never as instructions to send mail, execute code or modify files. Excel
strings are forced to text to prevent formula execution from document content.
Extracted data lives in the desktop SQLite database and chosen workbook; treat
both as customer data. The default `/exports/` folder is ignored by Git.

This feature uses the SQLite/PySide6 desktop workspace, independently of the
server's PostgreSQL/pgvector index. It does not change the FAQ reply model,
send email, make accounting decisions or publish customer data.

## Demo and verification

**Start Demo.cmd** includes a fictional invoice and contract. Its document
extraction uses an explicitly simulated provider and a temporary workbook; it
never contacts the real model. The regular CRM uses the local vision model.

Offline tests cover scanned/digital PDF rendering, image/Office readers,
extraction validation, cache reuse, changed/missing sources, correction review,
source path containment, malformed documents, cancellation, workbook formula
safety, preservation of unrelated/locked workbooks and the desktop flow.

Validation: **305 offline tests passed**, **16 PostgreSQL tests skipped** without
a configured test server, and **69/69 reply evaluation cases passed**. Document
checks use synthetic inputs and never read the regular customer database.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv/Scripts/python.exe -m pytest tests/test_documents.py -q
.venv/Scripts/python.exe -m tools.preview_desktop
```

For an explicit real-model test that generates its own fictional invoice scan
and contract, without opening `mini_crm.db` or `customer_files`:

```powershell
.venv/Scripts/python.exe -m tools.smoke_documents --provider local
```

It writes `docs/examples/document-register-synthetic.xlsx` and checks selected
invoice/contract fields. Screenshots:
[Excel register](previews/documents.png), [smaller window](previews/documents-small.png).

The real local-model run read the synthetic scanned invoice number/total and
contract expiry correctly. The contract date was originally in Facts; the
explicit-label mapping above makes that value available as a standard field on
subsequent exports. The saved actual-model workbook can be checked without
another inference run:

```powershell
.venv/Scripts/python.exe -m tools.smoke_documents --verify-existing
```

Smoke-test inputs are temporary; source paths in that example workbook are
illustrative after its test workspace closes. Regular CRM source files remain
in their persistent customer storage folders.

The provider follows Ollama's [vision API](https://docs.ollama.com/capabilities/vision)
and [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
The default [Qwen3-VL 2B Instruct model](https://ollama.com/library/qwen3-vl:2b-instruct)
is a downloaded vision model; other installed local vision models can be entered
in the model field. PDF rendering uses
[pypdfium2](https://pypdfium2.readthedocs.io/en/stable/python_api.html), serialized
because PDFium is not thread-safe.
