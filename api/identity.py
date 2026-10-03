"""Persistent installation credentials for the API-only multi-player server.

Credentials are random 256-bit client secrets, never game UUIDs or user IDs.
Only their SHA-256 digest is stored. Account creation and binding are atomic.
"""
import hashlib
import re
import threading
import time

from api.models import UserGetData


class CloudIdentity:
    def __init__(self, store):
        self.store = store
        # Bounded locks serialize an account's read/modify/save requests.
        self.locks = [threading.RLock() for _ in range(64)]
        with store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cloud_accounts (
                credential_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id),
                created_at INTEGER NOT NULL)''')

    @staticmethod
    def digest(secret):
        if not isinstance(secret, str) or not re.fullmatch(r'[a-f0-9]{64}', secret):
            raise PermissionError('missing or invalid client credential')
        return hashlib.sha256(bytes.fromhex(secret)).hexdigest()

    def resolve(self, secret, create=False):
        digest = self.digest(secret)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT user_id FROM cloud_accounts WHERE credential_hash=?', (digest,)).fetchone()
            if row:
                return int(row[0])
            if not create:
                raise PermissionError('account must log in first')
            user_id = int(db.execute('SELECT COALESCE(MAX(user_id),100000)+1 FROM users').fetchone()[0])
            data = UserGetData.create_default(user_id)
            self.store.save_user('cloud-' + digest, data, connection=db)
            db.execute('INSERT INTO cloud_accounts VALUES (?,?,?)', (digest, user_id, int(time.time())))
            return user_id

    def lock(self, user_id):
        return self.locks[user_id % len(self.locks)]

    def bind_existing(self, secret, user_id):
        """Offline migration: explicitly claim one existing, unbound save."""
        digest = self.digest(secret)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE user_id=?', (user_id,)).fetchone():
                raise ValueError('save does not exist')
            db.execute('INSERT INTO cloud_accounts VALUES (?,?,?)', (digest, user_id, int(time.time())))
