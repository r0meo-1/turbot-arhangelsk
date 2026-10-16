"""Short-lived per-owner write budget shared by all VK worker processes."""
import time

WINDOW_SECONDS = 60
MAX_WRITES = 30


def init_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS vk_miniapp_write_limits (
        user_id INTEGER PRIMARY KEY, window_start INTEGER NOT NULL,
        writes INTEGER NOT NULL)''')


def admit(cur, user_id, *, now=None):
    """Return Retry-After seconds, or zero; caller owns the write transaction."""
    now = int(time.time() if now is None else now)
    start = now // WINDOW_SECONDS * WINDOW_SECONDS
    cur.execute('DELETE FROM vk_miniapp_write_limits WHERE window_start < ?',
                (start - 2 * WINDOW_SECONDS,))
    cur.execute('''INSERT INTO vk_miniapp_write_limits VALUES (?,?,1)
        ON CONFLICT(user_id) DO UPDATE SET
        writes=CASE WHEN window_start=excluded.window_start THEN writes+1 ELSE 1 END,
        window_start=excluded.window_start''', (user_id, start))
    writes = cur.execute('SELECT writes FROM vk_miniapp_write_limits WHERE user_id=?',
                         (user_id,)).fetchone()[0]
    return max(1, start + WINDOW_SECONDS - now) if writes > MAX_WRITES else 0
