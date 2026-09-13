"""Inspect the Git index and archive contents before publishing.

Pass local private identifiers via --private-text; they are never written into
the report. Reports belong in ignored var/, not in the public repository.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import zipfile

BASE=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-text',action='append',default=[])
    p.add_argument('--report',type=Path)
    a=p.parse_args()
    git=['git','-c','safe.directory='+BASE.as_posix(),'-C',str(BASE)]
    entries=subprocess.check_output(git+['ls-files','--stage','-z']).decode('utf-8').split('\0')
    blobs={}
    for entry in filter(None,entries):
        metadata,name=entry.split('\t',1)
        _,oid,stage=metadata.split()
        if stage!='0':raise SystemExit('Unresolved Git index conflict')
        blobs[name]=oid
    private=[]
    for value in a.private_text:
        for variant in {value,value.replace('\\','/'),value.replace('/','\\')}:
            private.extend([variant.encode('utf-8').lower(),variant.encode('utf-16le').lower()])
    violations=[]; count=0; archive_members=0
    def inspect(label,data):
        if any(value in data.lower() for value in private):
            violations.append(dict(path=label,reason='private identifier in content'))
    packed=subprocess.check_output(git+['cat-file','--batch'],input=''.join(oid+'\n' for oid in blobs.values()).encode('ascii'))
    cursor=0
    for name,oid in blobs.items():
        if name.startswith(('var/','runtime/','dist/','resources/','client/','patches/')) or name.endswith(('.sqlite3','.p12','.keystore','.key','.pyc')):
            violations.append(dict(path=name,reason='local state or signing material in index'))
        if name.endswith(('.apk','.zip')):
            violations.append(dict(path=name,reason='client/runtime archive is outside server-only publication'))
        # Inspect the staged blob, not a potentially different working file.
        end=packed.index(b'\n',cursor)
        header=packed[cursor:end].split()
        assert header[0].decode('ascii')==oid and header[1]==b'blob'
        size=int(header[2]);cursor=end+1
        data=packed[cursor:cursor+size];cursor+=size+1; count+=1
        if len(data)>=100*1024*1024: violations.append(dict(path=name,reason='GitHub file limit'))
        inspect(name.encode('utf-8').decode(),name.encode('utf-8'))
        if name.endswith(('.apk','.zip')):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    archive_members+=1
                    inspect(name+'!'+info.filename,info.filename.encode('utf-8'))
                    inspect(name+'!'+info.filename,archive.read(info))
        else: inspect(name,data)
    report=dict(files_checked=count,archive_members_checked=archive_members,violations=violations)
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    raise SystemExit(bool(violations))


if __name__=='__main__':main()
