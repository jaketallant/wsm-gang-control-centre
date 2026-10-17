"""Cloud entrypoint. Waits safely until token exists and imports a SQLite backup once.

Does not modify existing Discord channels or roles. No bot token is logged.
"""
import os
import sys
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
IMPORT = DATA / 'wsm.import.sqlite3'
LIVE = DATA / 'wsm.sqlite3'
ARCHIVE = DATA / 'wsm.original-import.sqlite3'

def import_existing_database():
    DATA.mkdir(parents=True, exist_ok=True)
    if not IMPORT.exists():
        if not LIVE.exists():
            raise RuntimeError(
                'SAFETY STOP: The old WSM database has not been imported. '
                'Upload the backed-up wsm.import.sqlite3 into /app/data before enabling the bot. '
                'No empty replacement database will be created.'
            )
        return
    if LIVE.exists():
        raise RuntimeError('SAFETY STOP: Found both wsm.sqlite3 and wsm.import.sqlite3. '
                           'Refusing to overwrite any database. Back up both files and '
                           'resolve the duplicate in Railway volume files before restarting.')
    if ARCHIVE.exists():
        raise RuntimeError('SAFETY STOP: Existing original import archive. Inspect the volume.')
    with sqlite3.connect(f'file:{IMPORT}?mode=ro', uri=True) as src:
        check = src.execute('PRAGMA integrity_check').fetchone()
        if not check or check[0] != 'ok':
            raise RuntimeError('Imported database failed integrity check; nothing overwritten.')
        names = {row[0] for row in src.execute('SELECT name FROM sqlite_master WHERE type="table"')}
        needed = {'profiles','applications','panel_posts','points','activity_sessions'}
        if not needed.issubset(names):
            raise RuntimeError('Import does not look like a WSM database; nothing overwritten.')
        with sqlite3.connect(LIVE) as dest:
            src.backup(dest)
    IMPORT.rename(ARCHIVE)
    print('SUCCESS: Original WSM database imported into persistent storage. '
          'Original backup archived as wsm.original-import.sqlite3.',flush=True)

def main():
    print('WSM Railway Cloud Manager — existing server/roles remain unchanged.',flush=True)
    if not os.getenv('WSM_BOT_TOKEN'):
        print('MIGRATION MODE: no WSM_BOT_TOKEN supplied yet. '
              'Upload the existing WSM database to /app/data/wsm.import.sqlite3, '
              'then add WSM_GUILD_ID and WSM_BOT_TOKEN in Railway Variables. '
              'No Discord connection is attempted yet.',flush=True)
        while True:
            time.sleep(3600)
    if not os.getenv('WSM_GUILD_ID'):
        print('ERROR: WSM_GUILD_ID missing from Railway Variables.',flush=True)
        return 2
    try:
        import_existing_database()
    except Exception as exc:
        print('DATABASE IMPORT ERROR:',exc,flush=True)
        return 2
    from wsm_bot import main as bot_main
    return bot_main()

if __name__ == '__main__':
    sys.exit(main())
