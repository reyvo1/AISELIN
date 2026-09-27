import io
import subprocess
import tarfile
from pathlib import Path

import pytest

import agent.runner as runner


def _repo(tmp_path,monkeypatch):
    repo=tmp_path/'repo'; repo.mkdir()
    subprocess.run(['git','init'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','config','user.email','aioc@example.test'],cwd=repo,check=True)
    subprocess.run(['git','config','user.name','AIOC Test'],cwd=repo,check=True)
    (repo/'app.py').write_text('value = 1\n')
    subprocess.run(['git','add','.'],cwd=repo,check=True); subprocess.run(['git','commit','-m','baseline'],cwd=repo,check=True,capture_output=True)
    monkeypatch.setattr(runner,'ALLOWED_ROOTS',[tmp_path.resolve()])
    monkeypatch.setattr(runner,'ALLOWED_EXECUTABLES',{'git','python','python3','pytest'})
    return repo


def test_repo_context_read_search_patch_test_commit(tmp_path,monkeypatch):
    repo=_repo(tmp_path,monkeypatch); payload={'repo_path':str(repo)}
    context=runner._repo_context(payload); assert context['dirty'] is False and 'app.py' in context['tree']
    read=runner._repo_read({**payload,'path':'app.py'}); assert 'value = 1' in read['content'] and len(read['sha256'])==64
    search=runner._repo_search({**payload,'query':'value'}); assert search['matches'][0]['path']=='app.py'
    patch='--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n'
    applied=runner._repo_apply_patch({**payload,'patch':patch}); assert applied['ok'] and 'app.py' in applied['diffstat']
    tested=runner._repo_run_steps({**payload,'steps':[['python','-m','py_compile','app.py']]}); assert tested['ok']
    committed=runner._repo_commit({**payload,'message':'fix value','paths':applied['touched_paths']}); assert committed['committed'] is True
    # Only files touched by the AI patch are staged. Test/build artifacts stay uncommitted.
    assert subprocess.run(['git','diff','HEAD^','HEAD','--','app.py'],cwd=repo,text=True,capture_output=True).stdout
    assert all('app.py' not in line for line in committed['remaining_changes'])


def test_repo_rejects_path_escape_and_patch_escape(tmp_path,monkeypatch):
    repo=_repo(tmp_path,monkeypatch)
    with pytest.raises(RuntimeError): runner._repo_read({'repo_path':str(repo),'path':'../secret'})
    with pytest.raises(RuntimeError): runner._repo_apply_patch({'repo_path':str(repo),'patch':'--- a/../../bad\n+++ b/../../bad\n@@ -0,0 +1 @@\n+x\n'})


def test_safe_extract_rejects_traversal(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'ALLOWED_ROOTS',[tmp_path.resolve()])
    archive=tmp_path/'evil.tar.gz'
    with tarfile.open(archive,'w:gz') as tf:
        data=b'owned'; info=tarfile.TarInfo('../escape.txt'); info.size=len(data); tf.addfile(info,io.BytesIO(data))
    dest=tmp_path/'dest'
    with pytest.raises(RuntimeError): runner._safe_extract(archive,dest)
    assert not (tmp_path/'escape.txt').exists()


def test_repo_context_and_search_do_not_follow_symlinks(tmp_path,monkeypatch):
    repo=_repo(tmp_path,monkeypatch)
    secret=tmp_path/'secret.txt'; secret.write_text('VERY_SECRET_TOKEN\n')
    (repo/'escape.txt').symlink_to(secret)
    context=runner._repo_context({'repo_path':str(repo)})
    assert 'escape.txt' not in context['tree']
    search=runner._repo_search({'repo_path':str(repo),'query':'VERY_SECRET_TOKEN'})
    assert search['matches']==[]
    with pytest.raises(RuntimeError):
        runner._repo_read({'repo_path':str(repo),'path':'escape.txt'})


def test_server_service_and_docker_operations_default_deny_without_allowlists(monkeypatch):
    monkeypatch.setattr(runner,'ALLOWED_SERVICES',())
    monkeypatch.setattr(runner,'ALLOWED_CONTAINERS',())
    with pytest.raises(RuntimeError): runner._service_allowed('netmon.service')
    with pytest.raises(RuntimeError): runner._container_allowed('netmon')
    with pytest.raises(RuntimeError): runner._docker_list()


def test_repo_tools_hard_deny_secret_like_files_and_package_excludes_them(tmp_path,monkeypatch):
    repo=_repo(tmp_path,monkeypatch)
    (repo/'.env').write_text('API_KEY=super-secret\n')
    (repo/'private.pem').write_text('PRIVATE KEY\n')
    context=runner._repo_context({'repo_path':str(repo)})
    assert '.env' not in context['tree'] and 'private.pem' not in context['tree']
    assert runner._repo_search({'repo_path':str(repo),'query':'super-secret'})['matches']==[]
    with pytest.raises(RuntimeError): runner._repo_read({'repo_path':str(repo),'path':'.env'})
    with pytest.raises(RuntimeError):
        runner._repo_apply_patch({'repo_path':str(repo),'patch':'--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-API_KEY=super-secret\n+API_KEY=changed\n'})
    result,archive=runner._package_repo({'repo_path':str(repo),'build_steps':[],'test_steps':[],'artifact_excludes':[]})
    try:
        with tarfile.open(archive,'r:gz') as tf:
            names=set(tf.getnames())
        assert '.env' not in names and 'private.pem' not in names and 'app.py' in names
        assert result['ok'] is True
    finally: archive.unlink(missing_ok=True)

@pytest.mark.asyncio
async def test_deploy_configure_fetches_secrets_jit_and_links_env(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'ALLOWED_ROOTS',[tmp_path.resolve()])
    target=tmp_path/'app'; release=target/'releases'/'r1'; release.mkdir(parents=True)
    class FakeClient:
        async def secret(self,name):
            assert name=='db-password'
            return 's3cr3t value\nwith-newline'
    result=await runner._deploy_configure(FakeClient(),{
        'target_root':str(target),
        'env_relative_path':'shared/.env',
        'environment':{'APP_ENV':'production'},
        'secret_env':{'DB_PASSWORD':'db-password'},
    })
    env=target/'shared'/'.env'
    assert env.exists() and (env.stat().st_mode & 0o777)==0o600
    content=env.read_text()
    assert 'APP_ENV="production"' in content and 'DB_PASSWORD=' in content and 's3cr3t value' in content
    assert 's3cr3t value' not in str(result)
    activated=runner._activate({'release_path':str(release),'target_root':str(target),'service_type':'none','env_file':str(env),'link_env_to_release':True})
    assert activated['ok'] is True and (release/'.env').is_symlink()
    assert (release/'.env').resolve()==env.resolve()


def test_deploy_configure_rejects_env_symlink_escape(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'ALLOWED_ROOTS',[tmp_path.resolve()])
    target=tmp_path/'app'; target.mkdir(); outside=tmp_path/'outside'; outside.mkdir()
    (target/'shared').symlink_to(outside,target_is_directory=True)
    class FakeClient:
        async def secret(self,name): return 'x'
    with pytest.raises(RuntimeError):
        import asyncio
        asyncio.run(runner._deploy_configure(FakeClient(),{'target_root':str(target),'env_relative_path':'shared/.env','environment':{'A':'b'},'secret_env':{}}))
