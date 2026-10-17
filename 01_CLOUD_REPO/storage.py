"""Local SQLite storage for the WSM bot. Never stores Discord credentials."""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
          CREATE TABLE IF NOT EXISTS profiles(
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            character_name TEXT NOT NULL DEFAULT '', joined_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,user_id));
          CREATE TABLE IF NOT EXISTS applications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, answers TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
            reviewer_id INTEGER, reviewed_at INTEGER, review_message_id INTEGER);
          CREATE TABLE IF NOT EXISTS activity_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, started_at INTEGER NOT NULL, ended_at INTEGER);
          CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON
            activity_sessions(guild_id,user_id) WHERE ended_at IS NULL;
          CREATE TABLE IF NOT EXISTS achievements(
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            achievement TEXT NOT NULL, earned_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,user_id,achievement));
          CREATE TABLE IF NOT EXISTS meetings(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL,
            starts_at INTEGER NOT NULL, creator_id INTEGER NOT NULL,
            channel_id INTEGER, message_id INTEGER, remind_sent INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open');
          CREATE TABLE IF NOT EXISTS rsvps(
            meeting_id INTEGER NOT NULL, user_id INTEGER NOT NULL, response TEXT NOT NULL,
            PRIMARY KEY(meeting_id,user_id));
          CREATE TABLE IF NOT EXISTS missions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL,
            points INTEGER NOT NULL, creator_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', channel_id INTEGER, message_id INTEGER,
            created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS mission_members(
            mission_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(mission_id,user_id));
          CREATE TABLE IF NOT EXISTS points(
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,user_id));
          CREATE TABLE IF NOT EXISTS panel_posts(
            guild_id INTEGER NOT NULL, name TEXT NOT NULL,
            channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            PRIMARY KEY(guild_id,name));
          CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL, action TEXT NOT NULL, target_id INTEGER,
            description TEXT NOT NULL, created_at INTEGER NOT NULL);
        ''')
        self.conn.commit()

    def execute(self, sql, params=()):
        c = self.conn.execute(sql, params)
        self.conn.commit()
        return c

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def audit(self, guild_id, actor, action, target, note=''):
        self.execute('INSERT INTO audit_log(guild_id,actor_id,action,target_id,description,created_at) VALUES (?,?,?,?,?,?)',
                     (guild_id, actor, action, target, note, int(time.time())))

    def set_profile(self, guild, user, name):
        self.execute('''INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id)
            DO UPDATE SET character_name=excluded.character_name''', (guild, user, name, int(time.time())))

    def add_achievement(self, guild, user, name):
        return self.execute('INSERT OR IGNORE INTO achievements VALUES(?,?,?,?)',
                            (guild,user,name,int(time.time()))).rowcount > 0

    def clockin(self, guild, user):
        if self.one('SELECT 1 FROM activity_sessions WHERE guild_id=? AND user_id=? AND ended_at IS NULL',(guild,user)):
            return False
        self.execute('INSERT INTO activity_sessions(guild_id,user_id,started_at) VALUES(?,?,?)',
                     (guild,user,int(time.time())))
        return True

    def clockout(self, guild, user):
        return self.execute('UPDATE activity_sessions SET ended_at=? WHERE guild_id=? AND user_id=? AND ended_at IS NULL',
                            (int(time.time()),guild,user)).rowcount > 0

    def seconds(self, guild, user):
        row = self.one('''SELECT COALESCE(SUM(COALESCE(ended_at,?)-started_at),0) AS secs
           FROM activity_sessions WHERE guild_id=? AND user_id=?''', (int(time.time()),guild,user))
        return max(0, int(row['secs']))

    def attendance(self, guild, user):
        row = self.one('SELECT COUNT(*) AS cnt FROM activity_sessions WHERE guild_id=? AND user_id=?',(guild,user))
        return int(row['cnt'])

    def score(self, guild, user):
        row = self.one('SELECT points FROM points WHERE guild_id=? AND user_id=?',(guild,user))
        return int(row['points']) if row else 0

    def add_points(self, guild, user, delta):
        self.execute('''INSERT INTO points VALUES(?,?,?) ON CONFLICT(guild_id,user_id)
                DO UPDATE SET points=points+excluded.points''',(guild,user,delta))

    def complete_mission_atomic(self, guild, mission_id):
        """Atomically award signed-up users; None if already closed."""
        db=self.conn
        with db:
            mission=db.execute('SELECT status,points FROM missions WHERE id=? AND guild_id=?',
                               (mission_id,guild)).fetchone()
            if not mission or mission['status']!='open': return None
            users=[r['user_id'] for r in db.execute('SELECT user_id FROM mission_members WHERE mission_id=? AND completed=0',(mission_id,))]
            db.execute("UPDATE missions SET status='complete' WHERE id=?",(mission_id,))
            for uid in users:
                db.execute('UPDATE mission_members SET completed=1 WHERE mission_id=? AND user_id=?',(mission_id,uid))
                db.execute("INSERT INTO points VALUES(?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET points=points+excluded.points",(guild,uid,mission['points']))
        return users
