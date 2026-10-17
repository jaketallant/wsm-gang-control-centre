# WSM — Railway persistent Discord bot

This directory is the **only directory to upload to a private GitHub repository**.
See `../WSM_CLOUD_MIGRATION_STEPS.txt` for detailed end-user instructions.

## Runtime
Dockerfile executes `python -u cloud_start.py` as a continuous **worker**.
No HTTP server, keep-alive web request, public domain, or cron scheduling is needed.
Deploy one instance only. Attach a Railway **Volume** mounted at `/app/data`.

Variables in Railway (not in files or GitHub):
- `WSM_GUILD_ID`: the numeric Discord server ID for the existing WSM server
- `WSM_BOT_TOKEN`: the same Discord bot token you use locally

## Safe database migration
Deploy **without** the token first; the process stays running in MIGRATION MODE
without connecting to Discord or creating an empty DB. Stop the Windows bot,
run the provided local BACKUP_MY_WSM.bat, and upload **wsm.import.sqlite3**
into the Railway volume `/app/data`. Then add the variables, triggering a new
deployment. On first startup the SQLite backup is integrity-checked, copied
into `/app/data/wsm.sqlite3`, and original import is archived. No imported data
is checked into GitHub. If any live database is already present, the import
refuses to replace it automatically.

The source code is based on the original WSM Gang Control Centre bot. It keeps
the same slash commands and buttons. Do not run Windows and cloud bot together.
Do not rerun the one-time server builder or `/wsm_install` unless you intend
to refresh the bot-owned dashboard posts.

## Backup
Persistent volumes survive redeployment, but are NOT a substitute for backups.
Set Railway volume backups and occasionally download a consistent offsite copy.
SQLite is one writer; use one Railway replica. The app does not scale horizontally.
