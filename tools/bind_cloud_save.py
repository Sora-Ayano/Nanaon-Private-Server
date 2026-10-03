"""Bind a migrated save to a player's recovery code, with the server stopped."""
import argparse
import getpass
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,default=BASE/'var/data/users.sqlite3')
    p.add_argument('--user-id',type=int,required=True)
    a = p.parse_args()
    if not a.database.is_file(): p.error('database not found; migrate the save first')
    from api.storage import UserStore
    from api.identity import CloudIdentity
    from lan.state_lock import StateLock
    with StateLock(a.database.resolve().parent.parent):
        identity = CloudIdentity(UserStore(a.database))
        secret = getpass.getpass('玩家账号恢复码（64 位小写十六进制，不记录）：').strip()
        identity.bind_existing(secret,a.user_id)
        print('绑定完成。请妥善保存恢复码；持有码者可登录该账号。')


if __name__=='__main__': main()
