# Mini CRM architecture

Mini CRM is a small desktop learning project. The user interface will call plain
Python functions in the database layer. External services are hidden behind
provider interfaces, so the app can use offline fakes while we learn.

```text
                     +----------------------+
                     |      PySide6 UI      |
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     |  Application logic  |
                     |   and reply rules   |
                     +----------+-----------+
                                |
                +---------------+---------------+
                v                               v
       +------------------+             +------------------+
       | SQLite database  |             | Provider         |
       | customers, mail, |             | interfaces       |
       | FAQs, drafts,    |             +--------+---------+
       | files, audit log |                      |
       +------------------+             +--------+---------+
                                        | Fake / mock now
                                        | Cloud / Gmail later
                                        +------------------+
```

The database stores customer information, incoming emails, approved FAQ answers,
reply drafts, customer files, and an audit trail. `LLMProvider` and
`EmailProvider` describe what an external service can do without tying the rest
of the app to a vendor. The first milestone includes a deterministic fake LLM
and a JSON-backed email provider, so development can happen offline. The
customer screen uses functions in `app/db/customers.py`; SQL stays in that
database layer rather than in the PySide6 widgets. Later milestones will add
reply safety rules and more screens.
