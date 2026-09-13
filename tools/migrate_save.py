"""Copy a legacy SQLite save into this server, preserving player rows.

Stop both servers first. Source is read-only; an existing destination is never
replaced without --replace and a verified local backup.
"""
from contextlib import closing
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from lan.state_lock import StateLock


def quote(name):
    return '"'+name.replace('"','""')+'"'


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=10)


def copy_database(source,target):
    with closing(read_only(source)) as src, closing(sqlite3.connect(target)) as dst, dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('数据库完整性检查失败')


def player_snapshot(db):
    result={}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith(('sqlite_','master_')) or name in ('schema_migrations','content_catalog','asset_objects','server_settings'):
            continue
        columns=[row[1] for row in db.execute('PRAGMA table_info('+quote(name)+')')]
        rows=db.execute('SELECT '+','.join(map(quote,columns))+' FROM '+quote(name)).fetchall()
        result[name]=(columns,rows)
    return result


def fingerprint(snapshot):
    # repr preserves SQLite's bytes values; sorting makes row order irrelevant.
    return {name:hashlib.sha256(repr((cols,sorted(map(repr,rows)))).encode('utf-8')).hexdigest()
            for name,(cols,rows) in snapshot.items()}


def migrate(source,destination,replace=False,user_id=None):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise FileNotFoundError('旧数据库不存在')
    if source==destination:raise ValueError('源数据库与目标不能相同')
    state=destination.parent.parent
    with StateLock(state):
        if destination.exists() and not replace:
            raise FileExistsError('新目录已有存档。未做覆盖；确认替换时使用 --replace，工具会先备份。')
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='migration-',suffix='.sqlite3',dir=destination.parent)
        os.close(fd);staging=Path(name)
        try:
            copy_database(source,staging)
            with closing(sqlite3.connect(staging)) as db, db:
                if not {'user_id','uuid','name'}.issubset({r[1] for r in db.execute('PRAGMA table_info(users)')}):
                    raise ValueError('不是受支持的旧版 Nanaon SQLite 存档')
                before=player_snapshot(db)
                ids=[int(row[0]) for row in db.execute('SELECT user_id FROM users ORDER BY user_id')]
            if not ids:raise ValueError('旧数据库没有玩家账号，无需迁移')
            if user_id is None:
                if len(ids)==1:user_id=ids[0]
                elif 100004 in ids:user_id=100004
                else:raise ValueError('旧库有多个账号，请使用 --user-id 指定：'+','.join(map(str,ids)))
            if user_id not in ids:raise ValueError('指定的账号不在旧数据库中')
            from api.storage import UserStore
            UserStore(staging)  # Upgrade schema only in the temporary copy.
            with closing(sqlite3.connect(staging)) as db, db:
                db.execute('PRAGMA foreign_keys=OFF')
                for table,(columns,rows) in before.items():
                    db.execute('DELETE FROM '+quote(table))
                    sql='INSERT INTO '+quote(table)+' ('+','.join(map(quote,columns))+') VALUES ('+','.join('?' for _ in columns)+')'
                    db.executemany(sql,rows)
                db.commit()
                after={}
                for table,(columns,_) in before.items():
                    after[table]=(columns,db.execute('SELECT '+','.join(map(quote,columns))+' FROM '+quote(table)).fetchall())
                if fingerprint(before)!=fingerprint(after):raise ValueError('玩家数据对比失败，未迁移')
                if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('外键校验失败，未迁移')
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('迁移后完整性检查失败')
                db.execute("INSERT OR REPLACE INTO server_settings VALUES ('local_user_id',?)",(str(user_id),))
            # Keep both the selected account and target database backups local.
            stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup=None
            if destination.exists():
                backups=state/'backups';backups.mkdir(exist_ok=True)
                backup=backups/('users-before-migration-'+stamp+'.sqlite3')
                copy_database(destination,backup)
            # Rollback journals/WAL from a separately running old version are unsafe.
            if any(Path(str(destination)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
                raise RuntimeError('目标数据库仍有事务文件，请确认服务已停止并正常关闭数据库后重试。')
            os.replace(staging,destination)
            report=dict(user_id=user_id,users=len(ids),player_rows={name:len(rows) for name,(_,rows) in before.items()},
                        player_rows_preserved=True,source_modified=False,previous_destination_backed_up=backup is not None)
            (state/('migration-'+stamp+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
            return report
        finally:
            # Only the task-created temporary database is removed on failure.
            if staging.exists():staging.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,default=BASE/'var/data/users.sqlite3')
    parser.add_argument('--replace',action='store_true')
    parser.add_argument('--user-id',type=int)
    args=parser.parse_args()
    print(json.dumps(migrate(args.source,args.destination,args.replace,args.user_id),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
