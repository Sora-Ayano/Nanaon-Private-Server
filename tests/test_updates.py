import hashlib
import json
import pytest
from flask import Flask
from lan.updates import make_updates_blueprint
from tools.build_updates import build


def test_changed_file_only_and_range(tmp_path):
    content = b'updated-asset'; digest = hashlib.sha256(content).hexdigest()
    row = dict(kind='cache',path='Android/bundle/example/file',size=len(content),
               id=digest,sha256=digest,url='/bootstrap/files/'+digest,startup_check=True)
    fixed = dict(row,path='Android/bundle/unchanged/file',sha256='f'*64)
    old = dict(schema=1,locale='ja-JP',version_code=5465,revision='a'*64,files=[dict(row,sha256='0'*64),fixed])
    new = dict(old,revision='b'*64,files=[row,fixed])
    source = tmp_path/'source'; file = source/'cache'/row['path'];file.parent.mkdir(parents=True);file.write_bytes(content)
    output = tmp_path/'updates';package=build(old,new,source,output)
    assert package['files']==[row] and package['total_bytes']==len(content)
    app = Flask(__name__);app.register_blueprint(make_updates_blueprint(output));c=app.test_client()
    assert c.get('/bootstrap/updates.json?revision='+old['revision']+'&locale=ja-JP').json==package
    assert c.get('/bootstrap/updates.json?revision='+new['revision']+'&locale=ja-JP').status_code==204
    assert c.get('/bootstrap/updates.json?revision='+old['revision']+'&locale=zh-Hans').status_code==409
    assert c.get('/bootstrap/files/'+digest,headers={'Range':'bytes=0-3'}).data==b'upda'
    assert c.get('/bootstrap/files/private-save').status_code==404
    file = output/'files'/digest;file.write_bytes(b'changed')
    assert c.get('/bootstrap/files/'+digest).status_code==409
    with pytest.raises(ValueError):make_updates_blueprint(output)
