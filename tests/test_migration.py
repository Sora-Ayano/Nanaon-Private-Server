from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import pytest

from api.storage import UserStore
from api.models import UserGetData
from lan.state_lock import StateLock
from tools.migrate_save import migrate,player_snapshot,fingerprint


def old_save(path,user_id=777):
    store=UserStore(path)
    data=UserGetData.create_default(user_id)
    store.save_user('test-local-player',data)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE users SET name='Migration test', exp=1234, gem_total=4321, gem_free=4321")
        db.execute('UPDATE user_cards SET exp=321, skill_exp=123')
        db.execute('INSERT OR REPLACE INTO user_live_results (user_id,master_live_id,level,clear_count,high_score) VALUES (?,1,1,7,7654321)',(user_id,))
        # Exercise old saves which predate the destructive baseline migration.
        db.execute('DELETE FROM schema_migrations WHERE version>=3')
    return path


def test_migration_preserves_play_data_and_source(tmp_path):
    source=old_save(tmp_path/'old.sqlite3');destination=tmp_path/'new/var/data/users.sqlite3'
    digest=hashlib.sha256(source.read_bytes()).digest()
    with closing(sqlite3.connect(source)) as db, db:before=player_snapshot(db)
    report=migrate(source,destination)
    assert report['user_id']==777 and report['player_rows_preserved']
    assert hashlib.sha256(source.read_bytes()).digest()==digest
    with closing(sqlite3.connect(destination)) as db, db:
        after={table:(cols,db.execute('SELECT '+','.join('"'+c+'"' for c in cols)+' FROM "'+table+'"').fetchall()) for table,(cols,_) in before.items()}
        assert fingerprint(after)==fingerprint(before)
        assert db.execute("SELECT value FROM server_settings WHERE key='local_user_id'").fetchone()[0]=='777'
    # A normal subsequent startup must not run baseline resets again.
    UserStore(destination)
    with closing(sqlite3.connect(destination)) as db, db:
        assert db.execute('SELECT exp,gem_total FROM users').fetchone()==(1234,4321)
        assert db.execute('SELECT high_score,clear_count FROM user_live_results').fetchone()==(7654321,7)


def test_existing_destination_requires_replace_and_keeps_backup(tmp_path):
    source=old_save(tmp_path/'old.sqlite3');destination=tmp_path/'new/var/data/users.sqlite3'
    destination.parent.mkdir(parents=True);old_save(destination,user_id=888)
    with closing(sqlite3.connect(destination)) as db, db:before=fingerprint(player_snapshot(db))
    with pytest.raises(FileExistsError):migrate(source,destination)
    report=migrate(source,destination,replace=True)
    assert report['previous_destination_backed_up']
    backup=next((tmp_path/'new/var/backups').glob('*.sqlite3'))
    with closing(sqlite3.connect(backup)) as db, db:assert fingerprint(player_snapshot(db))==before


def test_migration_rejects_live_server_wrong_account_and_same_file(tmp_path):
    source=old_save(tmp_path/'old.sqlite3');destination=tmp_path/'var/data/users.sqlite3'
    with StateLock(tmp_path/'var'):
        with pytest.raises(RuntimeError,match='正在使用'):migrate(source,destination)
    with pytest.raises(ValueError,match='账号'):migrate(source,destination,user_id=42)
    with pytest.raises(ValueError,match='相同'):migrate(source,source)
    assert not destination.exists()


def test_migration_rejects_unrelated_sqlite(tmp_path):
    source=tmp_path/'unrelated.sqlite3'
    with closing(sqlite3.connect(source)) as db, db:db.execute('CREATE TABLE example(value)')
    with pytest.raises(ValueError,match='不.*支持'):migrate(source,tmp_path/'var/data/users.sqlite3')
