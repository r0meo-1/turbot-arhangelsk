import base64,importlib.util,json,os,subprocess
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('tgdeploy',Path('deploy/telegram_only.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(tmp_path):
    root=tmp_path/'app';root.mkdir();(root/'shared').mkdir()
    deps={'requirements.txt':b'x','shared/alean.py':b'x','shared/version.py':b'x','vk_bot.py':b'vk frozen'}
    for p,b in deps.items():(root/p).write_bytes(b)
    (root/'bot.py').write_bytes(b'old = True\n');(root/'.deployed-commit').write_text('b'*40)
    data=b'new = True\n'
    payload={'revision':'a'*40,'source':base64.b64encode(data).decode(),'sha256':m.digest(data),'dependencies':{p:m.digest(b) for p,b in deps.items()}}
    return root,payload


def test_telegram_only_deploy_is_idempotent_and_never_touches_vk(tmp_path,monkeypatch):
    root,p=fixture(tmp_path);calls=[];monkeypatch.setattr(os,'chown',lambda *a:None,raising=False)
    m.deploy(root,tmp_path/'state',p,run=lambda a,**kw:calls.append(a))
    assert (root/'bot.py').read_bytes()==b'new = True\n'
    assert (root/'vk_bot.py').read_bytes()==b'vk frozen'
    assert m.deploy(root,tmp_path/'state',p,run=lambda *a,**kw:pytest.fail('duplicate restart'))=='already_current'
    assert calls==[['systemctl','restart','turbot.service'],['systemctl','is-active','--quiet','turbot.service']]


def test_health_failure_restores_previous_code_and_revision(tmp_path,monkeypatch):
    root,p=fixture(tmp_path);calls=[];monkeypatch.setattr(os,'chown',lambda *a:None,raising=False)
    with pytest.raises(RuntimeError):
        m.deploy(root,tmp_path/'state',p,run=lambda a,**kw:calls.append(a),check=lambda r:(_ for _ in ()).throw(RuntimeError()))
    assert (root/'bot.py').read_bytes()==b'old = True\n'
    assert (root/'.deployed-commit').read_text()=='b'*40
    assert all('vk' not in ' '.join(a) for a in calls)


@pytest.mark.parametrize('change',['dependency','path','source','revision','missing'])
def test_incompatible_or_untrusted_payload_is_refused_before_mutation(tmp_path,change):
    root,p=fixture(tmp_path)
    if change=='dependency':(root/'shared/alean.py').write_text('different')
    elif change=='path':p['dependencies']['../outside.py']='x'
    elif change=='source':p['source']=base64.b64encode(b'bad').decode()
    elif change=='revision':p['revision']='main'
    else:p['dependencies'].pop('vk_bot.py')
    with pytest.raises((ValueError,OSError)):
        m.deploy(root,tmp_path/'state',p,run=lambda *a,**kw:pytest.fail('must not restart'))
    assert (root/'bot.py').read_bytes()==b'old = True\n'
