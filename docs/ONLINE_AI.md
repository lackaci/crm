# Online AI: no local model required

For the new email workflow, configure **Setup → Email AI** with
`nvidia/nemotron-3.5-lightning-free`; see [Email workflow](EMAIL_WORKFLOW.md). It uses local
FAQ retrieval and no embedding API. The vision/document settings and legacy
online FAQ option described below are independent and can incur charges.

The regular desktop CRM supports **Online — Vercel AI Gateway**, **Online — OpenAI**, an **OpenAI-compatible
API**, and optional **Local — Ollama** document extraction. New workspaces start
with the online option selected, with automatic processing off. Upgrades preserve
an existing local selection so previously enabled automation cannot silently
start uploading documents.

## Quick setup

1. Open **Open CRM.cmd**. Go to **Files → Excel register (all customers)**.
2. Select **Online — Vercel AI Gateway**. The preset fills in:
   - Model: `openai/gpt-5.4-mini`
   - FAQ embedding model: `openai/text-embedding-3-small`
   - Endpoint: `https://ai-gateway.vercel.sh/v1/chat/completions`
   - Key variable: `AI_GATEWAY_API_KEY`
   You can choose another gateway model that supports images and structured
   output. Gateway model IDs include the creator prefix.
3. Paste your API key into the masked **API key** field. Saving stores it in the
   OS keyring, clears the field and never writes it into SQLite or logs. You
   can instead set `AI_GATEWAY_API_KEY` in your private `.env` and restart the CRM.
   A saved key for the same provider/endpoint/variable takes precedence over
   the environment variable.
4. To use online AI for email replies too, check **Use this online provider for
   FAQ matching and reply suggestions too**. This uses online text embeddings
   and the selected completion model, including inbox automation and the saved
   answer preview. Leave it unchecked for the existing lightweight offline FAQ
   helper, which requires no Ollama runtime.
5. **Save settings**, then **Read files and update Excel**. Review/correct the
   results as before. Subsequent exports and unchanged documents reuse saved
   results without new inference calls.

No GPU, Ollama process or model download is needed for online mode. If you
previously started the optional Docker model service, you may stop it to free
resources without removing downloaded models:

```powershell
docker stop mini-crm-document-ollama
```

**Start Demo.cmd remains offline**, using fictional documents and a simulated
extractor regardless of saved online choices. Use the regular CRM for online
testing. You can still import the example emails and approved sample answers
with Mock mail while explicitly selecting online AI; mail mode and AI provider
are separate settings. Sending still requires the Gmail Live settings,
recipient rules and explicit approval.

## Other providers and local mode

For direct OpenAI access, choose **Online — OpenAI**, with its own key or
`OPENAI_API_KEY`. Its default model is `gpt-5.4-mini-2026-03-17`.
Existing saved provider choices are preserved. Switching to Vercel requires
saving that selection; it does not reuse your direct OpenAI key.

Choose **Online — compatible API** and open **Advanced online settings**. Enter
the provider's real HTTPS URL ending in `/chat/completions`, its vision model ID,
and a suitable API key variable (or paste a key for secure storage). The provider
must accept bearer authentication, inline image inputs and strict OpenAI-style
`json_schema` output. Compatibility varies; rejected requests are shown as
errors, with no silent fallback or automatic retries.

If also enabling FAQ suggestions, the compatible API must offer `/embeddings`
beside `/chat/completions`; set its embedding model ID in Advanced settings.
OpenAI uses `text-embedding-3-small` by default. Vercel uses
`openai/text-embedding-3-small` at `/v1/embeddings`. Advanced settings also show
the API key variable and the fixed official endpoint for either named provider.

Choose **Local — Ollama (optional)** to keep document content on your machine.
Use **Start Document Model.cmd** only for this option. Switching to local turns
off the online FAQ option. Local and online model IDs are retained separately
when switching choices within the settings panel.

## What goes online

Document extraction sends the selected file's extracted text and/or rendered
page image. Those originals can contain personal or financial data; choosing
online mode and starting a run opts into sending them to that API. Automatic
processing is a separate explicit opt-in and uses the saved provider.

FAQ matching and drafting apply the existing text-masking boundary, now including
email addresses as well as cards, IBANs and configured IDs. This is pattern
masking, not a guarantee that all identifying information is removed. Refunds,
complaints, money questions and unclear messages still escalate before a model
call. Only approved FAQ answers can produce a pending reply; unsafe additions
are rejected. Changing/reapproving a saved answer while a request runs cannot
commit a reply based on the old answer. Concurrent preparation reuses a pending
reply instead of creating another one.

API usage can incur provider charges. OpenAI Responses requests use `store:false`,
strict JSON schemas and no tools. This flag does not override the provider's
own retention policies. Keys never go in URL parameters, SQLite, examples or
audit messages. HTTPS requests disable environment proxies, redirects and
automatic retries. Authentication/quota/configuration failures stop a document
batch instead of repeating the same failing paid request for every file.
For Vercel, requests pass through AI Gateway to the selected model's provider;
gateway routing, retention and upstream failover policies also apply. The CRM's
no-retry setting controls its own HTTP client.

Outputs still need review. Online changes do not remove source-change checks,
safe Excel export, manual corrections, cancellation or duplicate protection.
Cache profiles include provider, endpoint, model and extraction schema; online
profiles identify configuration, not a downloadable model's weight digest.

## Verification and optional live test

Automated checks use fake HTTP responses and no paid APIs. They cover image/text
payloads, strict output validation, refusal/incomplete/error responses,
credentials, upgrade behavior, caching, masking, stale-answer protection,
background reply/preview tasks and demo isolation.

The Vercel integration is covered by mocked image extraction, embeddings,
approved reply generation, background UI tasks, credential isolation and saved
settings tests. No paid API calls were made during development verification.
The latest regression suite reports **383 passed, 16 skipped**, process exit 0.
The Vercel/online integration suite includes visible reply-error and retry checks.
The earlier full-suite shutdown abort was resolved by explicitly cleaning up
native Qt widgets after testing; see [verification details](INBOX_REFRESH.md).

To make an explicit paid online check with generated fictional documents only:

```powershell
.venv/Scripts/python.exe -m tools.smoke_documents --provider vercel
```

It uses your saved OS key or `AI_GATEWAY_API_KEY`, reads no regular customer files,
and writes `docs/examples/document-register-synthetic.xlsx`. To check the saved
example without any new API calls, use `--verify-existing`. An explicit local
check is still available with `--provider local`.

This change covers the **desktop** document, FAQ-matching and reply workflows.
The separate PostgreSQL API's pgvector/Ollama indexing worker has its own
configuration and is not migrated by this desktop option.

The Vercel integration follows its official documentation for
[Chat Completions](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions),
[structured output](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/structured-outputs)
and [embeddings](https://vercel.com/docs/ai-gateway/modalities/embeddings).
Its default [GPT-5.4 Mini gateway model](https://vercel.com/ai-gateway/models/gpt-5.4-mini)
accepts image input. Model access and usage charges depend on your gateway account.

The direct OpenAI implementation follows the official OpenAI documentation for
[image input](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses),
[structured output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
and [embeddings](https://developers.openai.com/api/docs/guides/embeddings).
The suggested [GPT-5.4 Mini model](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
supports image input and structured output; access depends on your API account.
