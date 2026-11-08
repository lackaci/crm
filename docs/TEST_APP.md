# The simpler test version

Double-click **Start Test.cmd**, or run:

```powershell
.venv/Scripts/python.exe -m app.test_main
```

It opens directly to **Test setup**. AI, Gmail, test messages, customer creation,
workflow mode and approval are configured on this one page. Use the AI, Gmail,
Test emails and Workflow jump buttons to move between sections. Approval and
Stop stay visible while you scroll. Inbox, Customers, Saved answers and Files
are still available for working with results.

## Try the entire motion offline

1. Leave **Offline simulation** checked. The test app starts this way on every
   launch, with fictional customers, messages and four approved saved answers.
2. Keep **Prepare and send replies**, five messages/run, maximum twenty messages
   for this approval. Click **Approve and simulate workflow**.
3. Read Workflow results and Generated replies. Two ordinary sample questions
   can produce simulated sends; unmatched senders and the risky refund example
   need attention. These are local simulations, with no Gmail or AI API calls.
4. To inspect pending replies before any simulated send, choose **Prepare replies
   only** and approve that workflow instead. Read the pending replies in Review
   replies. Completed simulated messages stay completed; add another sample
   email for a fresh test.
5. Under Test emails, enter the sender/customer details, subject and body, then
   **Add sample email**. Add a customer first if the address is new. Anna's
   example address already has a customer, so it can be used immediately.

Offline simulation deliberately disables model-based document reading. Turn it
off to test document extraction through the selected AI provider. Start Demo.cmd
continues to offer the separate disposable, simulated document walkthrough.

## Try online AI without setting up Gmail

Turn off Offline simulation. Under AI, choose Vercel AI Gateway, supply your key
through the masked field or `AI_GATEWAY_API_KEY`, check the online FAQ/reply
option and Save AI and document settings. Choose **Prepare replies only** and
approve the workflow. While Gmail is not connected, this can use the fictional
sample emails with your online model; calls may incur provider charges.

The AI section uses the same keyring storage, masking, validation and provider
options as the regular desktop app. Local Ollama remains optional for documents.
The actual Google OAuth client file is different from an AI Gateway API key.

## Try Gmail and live workflow sending

1. Turn off Offline simulation. In Gmail, enter your Gmail account and choose a
   Google Desktop OAuth client JSON stored outside the repository. The Gmail
   API/consent screen must already be configured in your Google project.
2. Review Import senders, lookback days and message limit. An empty Import senders
   field includes all senders in the date window. **Connect Gmail and import**
   signs in as needed and imports in the background, without leaving Test setup.
3. Add your test sender as a customer using the Test customer section, or link
   an imported unknown message in Inbox. Use a second test mailbox to send an
   eligible question to the connected account; sent/draft messages are excluded
   by the existing incoming-mail importer.
4. For Gmail drafts, enable draft permission, reconnect and choose **Create
   Gmail drafts**. For actual sending, also enable workflow sending and enter
   the permitted test sender addresses in **Send only to**, then reconnect.
5. Choose the action and message limits. The fixed summary shows the account,
   recipient allowlist and maximum messages. Click **Approve and send replies**
   (or Approve and run workflow for the other live actions).

That action approves the whole bounded workflow: import, match approved saved
answers, prepare valid replies, create Gmail drafts and send if that mode was
chosen. It does not ask for confirmation on every message. Only replies to the
connected account's imported Gmail messages can be delivered, and live sending
checks the configured recipient list before model generation and delivery.
Risky messages, missing customers, unmatched answers and unsafe edits need
attention. Generated/sent reply contents remain available under Workflow.

**Run again** reuses the same session approval and remaining budget. Optional
**Repeat import and workflow while this window is open** runs at the selected
interval. With Gmail connected, each run imports before processing the batch.
If that import fails, workflow approval is revoked rather than sending cached
replies in the background. A setting change requires a new approval.
The budget counts messages examined, including those needing attention. A
sender added as a customer after import is linked automatically when their
email uniquely matches that customer; ambiguous/unknown senders still need
explicit assignment.

## Stop, retries and isolation

- Stop / revoke approval blocks new operations and stops the repeat timer.
  A request already accepted remotely can finish; stopping cannot recall mail.
- Closing the app or changing AI/Gmail/workflow settings revokes approval.
  Neither live approval nor enabled repeat scheduling is restored at startup.
- Uncertain Gmail results use the existing durable outbox, stop the workflow
  and do not retry sending. Use Review replies -> Check Gmail status to reconcile.
  A new session approval cannot bypass an uncertain existing attempt.
- Existing manual edits must still fit the approved saved answer to be sent by
  this workflow; changing or deactivating that answer prevents delivery.
- Data is stored under ignored `test_workspace/`: `demo.sqlite`, `files/` and
  `exports/`. It never opens `mini_crm.db` or production customer_files. Back up
  that whole folder while closed if you want to keep test history.
- Gmail OAuth uses the test-specific `mini-crm-test` keyring service on first
  setup. AI keys can use the same environment variables or OS keyring as normal.
- The normal Open CRM.cmd and offline Start Demo.cmd keep their existing
  behavior. Session-wide sending is confined to this marked test workspace;
  normal desktop sending still uses its message confirmations. backend/ is unchanged.

## Verification

Tests use fake Gmail services, offline/fake models and isolated marked databases.
They cover workspace isolation, persistence, simulation, scoped live approval,
recipient restrictions, message limits, Stop, changed settings/answers,
uncertain sends, retry protection, secret-free UI status and the setup UI.
No real mailbox or paid model calls are needed for these checks.

Full regression run: **383 passed, 16 skipped**, process exit **0**. The final
**15 workflow checks** were also rechecked after the last test-app changes.
The command-line launcher passed with network access blocked, and both layout
sizes were rendered using fictional data. No real Gmail sends or paid AI calls
were made during implementation.

Previews: [Test setup](previews/test-setup.png),
[small window](previews/test-setup-small.png).
