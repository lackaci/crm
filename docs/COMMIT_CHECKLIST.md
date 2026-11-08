# Safe commit checklist

Run these commands from the repository root in PowerShell:

```powershell
git status --short
git diff --cached --name-status
git diff --cached -- . ':!*.db' ':!*.sqlite*'
git log --all --full-history -- .env credentials.json token.json
git grep -n -I -E '(^|[^A-Z])(sk-[A-Za-z0-9]|AIza|BEGIN .* PRIVATE KEY)' $(git rev-list --all)
git add <reviewed-files>
git diff --cached --name-status
git commit -m "Describe the reviewed change"
git tag -a m13-golden-evals -m "M13 golden evaluation baseline"
git status --short
```

Do not stage `.env`, databases, SQLite files, OAuth tokens, or credential JSON.
Review the staged file list and diff before committing; `reviews/` is intentionally
not ignored because review notes belong in version control.
