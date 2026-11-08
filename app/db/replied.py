"""Read-only history of confirmed Gmail replies and their original messages."""
from contextlib import closing
from .database import open_database

JOIN = 'FROM desktop_outbox o JOIN emails e ON e.id=o.email_id LEFT JOIN customers c ON c.id=e.customer_id'
FIELDS = ('o.id AS reply_id,o.email_id,o.sender,o.recipient,o.subject,o.body AS reply_body,'
          'o.updated_at AS sent_at,o.gmail_message_id,e.from_name,e.from_email,e.body AS incoming_body,'
          'e.subject AS incoming_subject,c.name AS customer_name')


def _where(search):
    where, args = "o.status='sent'", []
    if search.strip():
        literal = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        columns = ('e.from_name', 'e.from_email', 'e.subject', 'e.body', 'o.body', 'o.recipient')
        where += ' AND (' + ' OR '.join(column + " LIKE ? ESCAPE '\\'" for column in columns) + ')'
        args = [literal] * len(columns)
    return where, args


def count(path, search=''):
    where, args = _where(search)
    with closing(open_database(path)) as c:
        return c.execute('SELECT COUNT(*) ' + JOIN + ' WHERE ' + where, args).fetchone()[0]


def messages(path, search='', *, limit=100, offset=0):
    where, args = _where(search)
    with closing(open_database(path)) as c:
        return [dict(row) for row in c.execute('SELECT ' + FIELDS + ' ' + JOIN + ' WHERE ' + where +
            ' ORDER BY o.updated_at DESC,o.id DESC LIMIT ? OFFSET ?',
            [*args, min(200, max(1, int(limit))), max(0, int(offset))])]


def get_reply(path, reply_id):
    with closing(open_database(path)) as c:
        row = c.execute('SELECT ' + FIELDS + ' ' + JOIN + " WHERE o.id=? AND o.status='sent'", (reply_id,)).fetchone()
        return dict(row) if row else None
