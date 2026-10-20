# Mini CRM: Copilot agent workflow

## Setup (5 minutes)
1. Install VS Code + the GitHub Copilot and Copilot Chat extensions (sign in).
2. Create a folder `mini-crm`, copy ALL files from this zip into it, `git init`, open it in VS Code.
3. Open Copilot Chat (Ctrl+Alt+I / Cmd+Ctrl+I). At the bottom of the chat, open the
   agent dropdown. You should see: Lead, Architect, BackendDev, AIReplyDev, UIDev, QATeacher.
   (If not: Settings > search "chat.promptFiles" / "custom agents" and make sure they are enabled.
   Reload the window.)
4. Pick **Lead** and type:  /start-milestone 1

## How the flow works
- Lead plans and shows **handoff buttons** under its answer ("Start with Architect", etc.).
  Click one to pass the work to the next agent with a prepared prompt.
- Each worker agent, when done, offers a button to hand over to **QATeacher**.
- QATeacher tests, writes a plain-language note in LEARNING_NOTES.md, and hands back to Lead.
- You review, run the app, `git commit`, then start the next milestone.

## Run the app (after milestone 1)
    python -m venv .venv
    .venv\Scripts\activate        (Windows)   |   source .venv/bin/activate  (Mac/Linux)
    pip install PySide6 pytest
    python -m app.main

## Useful prompts
- /start-milestone 3      begin a milestone
- /explain-this           select code, then ask for a plain-language explanation
- Ask QATeacher anything: "why do we use an interface here?"

## Note on tool names
Tool names in the agent files (search, edit, runCommands...) can differ slightly between
VS Code versions. If an agent can't edit files or run commands, click the tools icon in
the chat box and enable them for that agent, or adjust the `tools:` line.
