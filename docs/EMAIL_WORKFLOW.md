# Gmail → AI reply → approve → send

Open **Open CRM.cmd**. Use **Setup: Gmail and AI** to configure everything for
email in one page. This is the normal desktop workflow; it uses the existing
SQLite workspace. The separate server backend is unchanged.

## First setup

1. Under **Gmail connection**, enter your Gmail address and choose a Google
   Desktop OAuth client JSON stored outside the repository. Connect/reconnect.
   Leave write/send permissions off for read-only testing. Set import senders,
   lookback days and the batch limit; an empty sender restriction includes all
   senders. See [Google setup](GMAIL_DESKTOP.md).
2. Under **Email AI**, keep **nvidia/nemotron-3.5-lightning-free** and **Free model only**.
   Put your Vercel **AI Gateway key** in the masked field and save, or set
   `AI_GATEWAY_API_KEY` in `.env` and restart. A general Vercel account token
   is not an AI Gateway key. Saved keys use the OS keyring; SQLite stores only
   configuration references. An existing saved key takes precedence over `.env`.
3. Click **Save email AI**, then **Test AI connection**. This sends a fixed
   fictional question; it does not send a customer message. Check the visible result.
4. Leave **Prepare replies automatically after importing** checked. Review the
   setup summary and click **Start 5-minute checks**. The first check is immediate;
   later checks are every **300 seconds** while the app is open or minimized.
   Closing the app stops checks. After sleep there is one overdue check, without
   a burst of missed checks. Saved workflow settings resume on the next launch.
5. For sending, enable Gmail draft permission and live sending, enter permitted
   recipients, then reconnect to grant Google consent. Importing never requires
   these permissions. The compose OAuth scope also permits sending at Google;
   the CRM enforces its separate sending switch and recipient list.

For import-only use, turn off automatic replies before starting; an AI key is
then unnecessary. **Check Gmail now** in Setup and **Import from Gmail** in Inbox
use the same incremental importer. **Pause checks** stops new scheduled work;
an already-running operation may finish. The Gmail **Pause mail imports and
delivery** switch also blocks sending. Neither pause recalls an accepted email.

## Everyday use

Inbox displays connection state, last/next check, import progress and results.
The main menu shows Inbox, Review replies, Setup and **Replied**. **More tools** reveals
Customers, Saved answers, Files and advanced Automation.
Messages appear newest first; search and counted quick filters still work.
Manual import clears filters that might hide results; scheduled import preserves
your current view. Gmail history and message IDs avoid repeated downloads.
Partial imports keep successful messages, and capped backlogs remain on disk.
Two app instances share a database lease so they cannot import concurrently.

Each imported unfinished message gets a durable AI job. Batches run in workers,
then the remaining queue drains without waiting another five minutes. Failed
jobs retry with a delay; unfinished jobs can resume after their worker lease
expires. Existing pending replies and human edits are preserved. Optional
category/staff routing still runs when enabled in advanced Automation.

Click **Review reply**, or open **Review replies** in the menu:

1. Read the incoming message, suggested reply and supporting approved facts.
2. Edit if necessary. **Save changes** saves locally. **Regenerate AI reply** is
   explicit, replaces an editable suggestion and removes its previous approval.
3. Click **Approve reply**. This saves and approves the visible text for the
   shown recipient/account. Approval alone never delivers anything.
4. Click **Send reply**. The worker creates a Gmail draft internally and sends
   the exact approved content. There is no separate Create Gmail draft action
   or second content confirmation in this normal workflow.

Sending remains disabled until approval, Google permission, live sending,
recipient and account checks pass. Editing—even editing then undoing—removes
approval. Relevant source/account/recipient changes also invalidate it. A
successful Gmail response marks the message Sent; acceptance does not guarantee
recipient delivery. Outgoing replies remain plain text, one recipient, no attachments.

After Gmail confirms sending, the original email disappears from every Inbox
filter, including Needs assignment and unread counts. Open **Replied** in the
main menu to find all confirmed replies, including earlier sends. It shows the
recipient, subject and sent time, with the original email and exact sent response
in a read-only pane. Search covers both original messages and replies; history
is paginated and kept after restarting. Messages and files are not deleted.
Approval and unsent drafts stay in the active workflow. Failed or uncertain sends
remain active until Gmail confirms success through sending or Check Gmail status.

Missing facts and sensitive requests produce a clarification for personal
review. **Write reply / review existing** opens a manual editor even without
approved answers or a linked customer. Unknown senders stay visible and can be
linked directly; the app creates no customer automatically. The AI cannot send.

If a send times out, use **Check Gmail status**. Do not make another reply to
retry delivery. Reapproval, restarting or repeated clicks cannot bypass the
durable uncertainty lock. Cancelling a confirmed staged Gmail draft unlocks
editing. An absent search result does not prove Gmail never sent the message.

## Free model and data handling

The selected model is `nvidia/nemotron-3.5-lightning-free`. Vercel states that
NVIDIA logs prompts and responses for model improvement and restricts this free
endpoint to non-sensitive trial use. Do not use it for personal/confidential
customer information, production or business-critical systems. Masking below
does not make real customer messages suitable for this trial.
The CRM keeps the explicit `-free` ID and never chooses a paid fallback. Authentication,
quota or model-availability errors pause AI generation while Gmail imports and
manual replies remain available. Fix the settings and **Test AI connection** to
resume. Changing to another model requires an explicit setting change.

Email matching uses local lexical retrieval over approved saved answers and
bounded customer/conversation context. It makes **no embedding API calls**.
The independent document/vision model under Files is unchanged; document reading
may still incur charges under its own configuration.

Supported email/card/IBAN/ID patterns are masked before online drafting. This is
not complete anonymization: document text, names and other personal information
can still reach Vercel and its provider. Review current provider terms before
processing real customer mail. Sources and numeric/link checks reduce mistakes;
they do not guarantee factual accuracy. Human review is required.

Official references checked 8 November 2026:
[Nemotron Free model and trial restrictions](https://vercel.com/ai-gateway/models/nemotron-3.5-lightning-free),
[Gateway pricing](https://vercel.com/docs/ai-gateway/pricing).

## Manual verification

After upgrading from Beam Free, restart the app. Your stored selection is
migrated to Nemotron; automatic AI drafting pauses until you use **Test AI
connection** in Setup. Gmail settings and existing replies are preserved.
Use fictional, non-sensitive mail for this provider's trial tests.

1. **Offline:** Start Demo.cmd → opening-hours email → Prepare reply → inspect
   sources → edit → Approve reply. Send stays disabled. Repeat with the refund
   example and without approved answers to see the manual clarification path.
2. **Setup:** Open CRM.cmd → Setup → connect a test Gmail account, save/test AI,
   start checks. Confirm the summary, last/next check and 5-minute interval.
3. **Automatic flow:** From a second test account send a harmless opening-hours
   question covered by an approved saved answer. Wait for the next check. Verify
   one new message and one suggestion. The interface should remain responsive.
4. **Approval:** Edit and approve it; check Send becomes available. Type an edit
   and undo it; Send must stay disabled until you approve again.
5. **Sending:** Allow that test recipient and grant sending permission. Click
   Approve then Send once. Verify exactly one threaded reply in Gmail and Sent
   under **Replied** in the CRM. Confirm the original disappears from Inbox and
   Needs assignment, then inspect the original and sent response in Replied.
   Restart and confirm the history remains. No scheduled operation should send
   any other reply.
6. **Recovery:** Check again for duplicate skipping, then pause checks. An AI
   configuration error should leave imported messages and manual replies usable.
   Preserve any uncertain send and use Check Gmail status instead of resending.

Automated tests use fake Gmail and mocked HTTP, with real network blocked.
The full suite reports **419 passed, 16 skipped**, process exit **0**; no
post-success Qt exit error occurred. The skipped tests require the separate
PostgreSQL backend. After switching to Nemotron, 61 focused workflow/online-AI
tests pass with exit code 0, including two model-migration checks. The workflow
now has 29 dedicated checks, including GUI
approval/sending, independent workers, restart recovery and free-model failures.
Seven further sent-history checks cover Inbox removal/counts, preserved history,
failed/uncertain sends, search/paging and the send-to-Replied GUI path after restart.
The offline demo entry point and fictional preview utility also exit successfully.
Previews: [Setup](previews/email-setup.png),
[Gmail settings](previews/email-setup-gmail.png),
[Approve and Send](previews/email-approval.png),
[900×620 layout](previews/email-approval-small.png),
[Reply history](previews/email-replied.png),
[Reply history at 900×620](previews/email-replied-small.png).
Live OAuth, the current free model and delivery to actual test accounts still
need the above account-based verification; implementation tests do not prove them.
