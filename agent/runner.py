#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

CENTRAL = os.getenv("AIOC_CENTRAL_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("AIOC_AGENT_TOKEN", "")
AGENT_ID = os.getenv("AIOC_AGENT_ID", socket.gethostname())
AGENT_VERSION = os.getenv("AIOC_AGENT_VERSION", "0.9.0-dev")
POLL_SECONDS = max(1, int(os.getenv("AIOC_AGENT_POLL_SECONDS", "3")))
ALLOWED_ROOTS = [Path(x).expanduser().resolve() for x in os.getenv("AIOC_AGENT_ALLOWED_ROOTS", str(Path.home())).split(",") if x.strip()]
ALLOWED_EXECUTABLES = {x.strip() for x in os.getenv("AIOC_AGENT_ALLOWED_EXECUTABLES", "git,python,python3,pytest,npm,pnpm,yarn,composer,php").split(",") if x.strip()}
ALLOWED_SERVICES = tuple(x.strip() for x in os.getenv("AIOC_AGENT_ALLOWED_SERVICES", "").split(",") if x.strip())
ALLOWED_CONTAINERS = tuple(x.strip() for x in os.getenv("AIOC_AGENT_ALLOWED_CONTAINERS", "").split(",") if x.strip())
ALLOW_NGINX = os.getenv("AIOC_AGENT_ALLOW_NGINX", "false").lower() in {"1","true","yes","on"}
NGINX_SITES_DIR = Path(os.getenv("AIOC_AGENT_NGINX_SITES_DIR", "/etc/nginx/conf.d")).resolve()
ALLOWED_HEALTH_HOSTS = {x.strip().lower() for x in os.getenv("AIOC_AGENT_ALLOWED_HEALTH_HOSTS", "127.0.0.1,localhost,::1").split(",") if x.strip()}
MAX_COMMAND_SECONDS = max(10, int(os.getenv("AIOC_AGENT_MAX_COMMAND_SECONDS", "900")))
ALLOW_PACKAGE_UPDATES = os.getenv("AIOC_AGENT_ALLOW_PACKAGE_UPDATES", "false").lower() in {"1","true","yes","on"}
ALLOWED_PACKAGES = tuple(x.strip() for x in os.getenv("AIOC_AGENT_ALLOWED_PACKAGES", "").split(",") if x.strip())

CAPABILITIES = [x.strip() for x in os.getenv(
    "AIOC_AGENT_CAPABILITIES",
    "system.status,repo.inspect,repo.context,repo.read,repo.search,repo.apply_patch,repo.run_steps,repo.commit,repo.prepare_release",
).split(",") if x.strip()]

PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:-]{0,127}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,180}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
SENSITIVE_COMPONENTS={".ssh",".aws",".gnupg",".kube"}
SENSITIVE_NAMES={"id_rsa","id_ed25519","credentials","credentials.json","service-account.json"}
SENSITIVE_PATTERNS=(".env",".env.*","*.pem","*.key","*.p12","*.pfx")


def _is_sensitive_relative(value: str) -> bool:
    rel=Path(value)
    if any(part in SENSITIVE_COMPONENTS for part in rel.parts): return True
    name=rel.name.lower()
    if name in SENSITIVE_NAMES: return True
    return any(fnmatch.fnmatch(name,pattern) for pattern in SENSITIVE_PATTERNS)


def _safe_path(value: str, *, must_exist: bool = True) -> Path:
    path = Path(value).expanduser().resolve(strict=must_exist)
    if not any(path == root or root in path.parents for root in ALLOWED_ROOTS):
        raise RuntimeError(f"path outside agent allow-list: {path}")
    return path


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = MAX_COMMAND_SECONDS, allowed: bool = True) -> dict[str, Any]:
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise RuntimeError("invalid argv")
    exe = Path(argv[0]).name
    if allowed and exe not in ALLOWED_EXECUTABLES:
        raise RuntimeError(f"executable is not allowed by agent policy: {exe}")
    proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout, shell=False)
    result = {"argv": argv, "returncode": proc.returncode, "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:]}
    if proc.returncode != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result


def _system_status() -> dict[str, Any]:
    memory: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1); memory[key] = int(raw.strip().split()[0]) * 1024
    except Exception:
        pass
    disk = shutil.disk_usage("/")
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0,0.0,0.0)
    return {
        "ok": True, "hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(),
        "load": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "memory": {"total": memory.get("MemTotal"), "available": memory.get("MemAvailable")},
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "uptime_seconds": _uptime(),
    }


def _uptime() -> float | None:
    try: return float(Path("/proc/uptime").read_text().split()[0])
    except Exception: return None



def _process_list(payload: dict[str, Any]) -> dict[str, Any]:
    limit=max(1,min(int(payload.get("limit",30)),200)); sort=str(payload.get("sort") or "cpu")
    sort_key={"cpu":"-%cpu","memory":"-%mem","pid":"pid"}.get(sort)
    if not sort_key: raise RuntimeError("unsupported process sort")
    result=_run(["ps","-eo","pid,ppid,user,%cpu,%mem,etime,comm,args","--sort",sort_key],allowed=False,timeout=30)
    lines=result["stdout"].splitlines()
    return {"ok":True,"sort":sort,"processes":lines[:limit+1],"count":max(0,min(limit,len(lines)-1))}


def _network_sockets(payload: dict[str, Any]) -> dict[str, Any]:
    argv=["ss","-lntup"] if bool(payload.get("listening_only",True)) else ["ss","-ntup"]
    result=_run(argv,allowed=False,timeout=30)
    return {"ok":True,"listening_only":bool(payload.get("listening_only",True)),"sockets":result["stdout"].splitlines()[:1000]}


def _network_routes() -> dict[str, Any]:
    result=_run(["ip","route","show"],allowed=False,timeout=30)
    return {"ok":True,"routes":result["stdout"].splitlines()[:500]}


def _firewall_status() -> dict[str, Any]:
    if shutil.which("ufw"):
        proc=subprocess.run(["ufw","status","verbose"],text=True,capture_output=True,timeout=30,shell=False)
        return {"ok":proc.returncode==0,"backend":"ufw","stdout":proc.stdout[-20000:],"stderr":proc.stderr[-4000:]}
    if shutil.which("nft"):
        proc=subprocess.run(["nft","list","ruleset"],text=True,capture_output=True,timeout=30,shell=False)
        return {"ok":proc.returncode==0,"backend":"nft","stdout":proc.stdout[-30000:],"stderr":proc.stderr[-4000:]}
    return {"ok":False,"backend":"none","message":"ufw/nft not available"}


def _apt_simulation() -> dict[str, Any]:
    if not shutil.which("apt-get"): raise RuntimeError("apt-get is not available")
    proc=subprocess.run(["apt-get","-s","upgrade"],text=True,capture_output=True,timeout=120,shell=False,env={**os.environ,"LC_ALL":"C"})
    if proc.returncode!=0: raise RuntimeError(proc.stderr[-4000:] or "apt simulation failed")
    upgrades=[]
    for line in proc.stdout.splitlines():
        if line.startswith("Inst "):
            parts=line.split()
            if len(parts)>=2: upgrades.append(parts[1])
    return {"ok":True,"packages":sorted(set(upgrades))[:1000],"count":len(set(upgrades)),"stdout":proc.stdout[-20000:]}


def _security_updates() -> dict[str, Any]:
    simulated=_apt_simulation(); security=[]
    if shutil.which("apt-get"):
        proc=subprocess.run(["apt-get","-s","upgrade"],text=True,capture_output=True,timeout=120,shell=False,env={**os.environ,"LC_ALL":"C"})
        for line in proc.stdout.splitlines():
            if line.startswith("Inst ") and ("security" in line.lower() or "ubuntu-security" in line.lower()):
                parts=line.split()
                if len(parts)>=2: security.append(parts[1])
    return {"ok":True,"packages":sorted(set(security)),"count":len(set(security)),"all_upgradable_count":simulated["count"]}


def _apply_package_updates(payload: dict[str, Any]) -> dict[str, Any]:
    if not ALLOW_PACKAGE_UPDATES: raise RuntimeError("package updates are disabled until AIOC_AGENT_ALLOW_PACKAGE_UPDATES=true")
    packages=[str(x) for x in (payload.get("packages") or [])]
    if not packages or len(packages)>50: raise RuntimeError("explicit package list is required (max 50)")
    for package in packages:
        if not PACKAGE_RE.fullmatch(package): raise RuntimeError(f"invalid package name: {package}")
        if ALLOWED_PACKAGES and package not in ALLOWED_PACKAGES: raise RuntimeError(f"package outside allow-list: {package}")
    result=_run(["apt-get","install","--only-upgrade","-y","--",*packages],allowed=False,timeout=900)
    return {"ok":True,"packages":packages,"stdout":result["stdout"],"stderr":result["stderr"]}

def _repo_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _safe_path(str(payload["repo_path"]))
    if not (repo / ".git").exists(): raise RuntimeError("repository does not contain .git")
    revision = _run(["git","rev-parse","HEAD"], cwd=repo)["stdout"].strip()
    branch = _run(["git","branch","--show-current"], cwd=repo)["stdout"].strip()
    status = _run(["git","status","--porcelain=v1"], cwd=repo)["stdout"]
    return {"ok":True,"revision":revision,"branch":branch,"dirty":bool(status.strip()),"changes":status.splitlines()[:500]}


def _repo_relative(repo: Path, value: str, *, must_exist: bool=True) -> Path:
    rel=Path(value)
    if rel.is_absolute() or ".." in rel.parts or ".git" in rel.parts or _is_sensitive_relative(rel.as_posix()):
        raise RuntimeError("repository path is not allowed")
    path=(repo/rel).resolve(strict=must_exist)
    if path != repo and repo not in path.parents:
        raise RuntimeError("repository path escaped root")
    return path


def _repo_context(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"]))
    base=_repo_inspect(payload)
    tree=[]; stack=[]
    ignored={".git","node_modules",".venv","venv","__pycache__","dist","build","vendor"}
    for path in repo.rglob("*"):
        try: rel=path.relative_to(repo)
        except ValueError: continue
        if any(part in ignored for part in rel.parts) or _is_sensitive_relative(rel.as_posix()): continue
        if path.is_symlink(): continue
        if path.is_file():
            try:
                resolved=path.resolve()
                if resolved != repo and repo not in resolved.parents: continue
            except Exception: continue
            tree.append(rel.as_posix())
            if len(tree)>=2500: break
    detectors={"package.json":"node","composer.json":"php-composer","artisan":"laravel","pyproject.toml":"python","requirements.txt":"python","Dockerfile":"docker","docker-compose.yml":"docker-compose","compose.yml":"docker-compose","go.mod":"go","Cargo.toml":"rust"}
    for filename,label in detectors.items():
        if (repo/filename).exists(): stack.append(label)
    return {**base,"tree":tree,"tree_truncated":len(tree)>=2500,"detected_stack":sorted(set(stack))}


def _repo_read(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"])); path=_repo_relative(repo,str(payload.get("path") or ""))
    if not path.is_file(): raise RuntimeError("repository path is not a file")
    if path.stat().st_size>300_000: raise RuntimeError("repository file exceeds read limit")
    raw=path.read_bytes(); content=raw.decode("utf-8",errors="replace")
    return {"ok":True,"path":path.relative_to(repo).as_posix(),"sha256":hashlib.sha256(raw).hexdigest(),"content":content}


def _repo_search(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"])); query=str(payload.get("query") or "")
    if not query or len(query)>500: raise RuntimeError("invalid repository search query")
    ignored={".git","node_modules",".venv","venv","__pycache__","dist","build","vendor"}; matches=[]
    for path in repo.rglob("*"):
        if path.is_symlink() or not path.is_file(): continue
        try:
            resolved=path.resolve()
            if resolved != repo and repo not in resolved.parents: continue
        except Exception: continue
        rel=path.relative_to(repo)
        if any(part in ignored for part in rel.parts) or _is_sensitive_relative(rel.as_posix()): continue
        try:
            if path.stat().st_size>500_000: continue
            text=path.read_text(errors="ignore")
        except Exception: continue
        for lineno,line in enumerate(text.splitlines(),1):
            if query.lower() in line.lower():
                matches.append({"path":rel.as_posix(),"line":lineno,"text":line[:1000]})
                if len(matches)>=250: return {"ok":True,"query":query,"matches":matches,"truncated":True}
    return {"ok":True,"query":query,"matches":matches,"truncated":False}


def _validate_patch_paths(patch: str) -> list[str]:
    paths=set()
    for line in patch.splitlines():
        values=[]
        if line.startswith("+++ ") or line.startswith("--- "):
            values=[line[4:].strip().split("\t",1)[0]]
        elif line.startswith("diff --git "):
            parts=line.split()
            if len(parts)>=4: values=[parts[2],parts[3]]
        for value in values:
            if value=="/dev/null": continue
            if value.startswith(("a/","b/")): value=value[2:]
            path=Path(value)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts or not value or _is_sensitive_relative(path.as_posix()):
                raise RuntimeError("patch contains disallowed path")
            paths.add(path.as_posix())
    if not paths: raise RuntimeError("patch contains no repository file paths")
    return sorted(paths)


def _repo_apply_patch(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"])); patch=str(payload.get("patch") or "")
    if not patch or len(patch)>1_000_000: raise RuntimeError("patch is empty or too large")
    touched=_validate_patch_paths(patch)
    check=subprocess.run(["git","apply","--check","--whitespace=nowarn","-"],cwd=str(repo),input=patch,text=True,capture_output=True,timeout=120,shell=False)
    if check.returncode!=0: raise RuntimeError(f"git apply check failed: {check.stderr[-4000:]}")
    apply=subprocess.run(["git","apply","--whitespace=nowarn","-"],cwd=str(repo),input=patch,text=True,capture_output=True,timeout=120,shell=False)
    if apply.returncode!=0: raise RuntimeError(f"git apply failed: {apply.stderr[-4000:]}")
    status=_run(["git","status","--porcelain=v1"],cwd=repo)["stdout"]
    diffstat=_run(["git","diff","--stat"],cwd=repo)["stdout"]
    return {"ok":True,"changes":status.splitlines()[:500],"diffstat":diffstat[-10000:],"touched_paths":touched}

def _repo_run_steps(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"])); steps=payload.get("steps") or []
    if not isinstance(steps,list) or len(steps)>50: raise RuntimeError("invalid test step list")
    results=[]
    for argv in steps:
        if not isinstance(argv,list): raise RuntimeError("test step must be argv array")
        results.append(_run([str(x) for x in argv],cwd=repo))
    return {"ok":True,"steps":results,"count":len(results)}


def _repo_commit(payload: dict[str, Any]) -> dict[str, Any]:
    repo=_safe_path(str(payload["repo_path"])); message=str(payload.get("message") or "AIOC autonomous change")[:240]
    paths=[str(x) for x in (payload.get("paths") or [])]
    if paths:
        safe=[]
        for value in paths:
            path=_repo_relative(repo,value,must_exist=False); safe.append(path.relative_to(repo).as_posix())
        _run(["git","add","--",*safe],cwd=repo)
    else:
        _run(["git","add","-u"],cwd=repo)
    staged=subprocess.run(["git","diff","--cached","--quiet"],cwd=str(repo),shell=False)
    if staged.returncode==0:
        revision=_run(["git","rev-parse","HEAD"],cwd=repo)["stdout"].strip()
        remaining=_run(["git","status","--porcelain=v1"],cwd=repo)["stdout"].splitlines()
        return {"ok":True,"revision":revision,"committed":False,"message":"no staged changes","paths":paths,"dirty":bool(remaining),"remaining_changes":remaining[:500]}
    if staged.returncode!=1: raise RuntimeError("unable to inspect staged changes")
    _run(["git","commit","-m",message],cwd=repo)
    revision=_run(["git","rev-parse","HEAD"],cwd=repo)["stdout"].strip()
    remaining=_run(["git","status","--porcelain=v1"],cwd=repo)["stdout"].splitlines()
    return {"ok":True,"revision":revision,"committed":True,"paths":paths,"dirty":bool(remaining),"remaining_changes":remaining[:500]}

def _should_exclude(relative: str, excludes: list[str]) -> bool:
    if _is_sensitive_relative(relative): return True
    parts = Path(relative).parts
    for pattern in excludes:
        if pattern in parts or fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(Path(relative).name, pattern):
            return True
    return False


def _package_repo(payload: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    repo = _safe_path(str(payload["repo_path"]))
    build_results=[]; test_results=[]
    for argv in payload.get("build_steps", []): build_results.append(_run(list(argv), cwd=repo))
    for argv in payload.get("test_steps", []): test_results.append(_run(list(argv), cwd=repo))
    excludes=[str(x) for x in payload.get("artifact_excludes", [])]
    fd, temp_name = tempfile.mkstemp(prefix="aioc-release-", suffix=".tar.gz"); os.close(fd)
    temp = Path(temp_name)
    with tarfile.open(temp, "w:gz") as archive:
        for path in repo.rglob("*"):
            rel = path.relative_to(repo).as_posix()
            if _should_exclude(rel, excludes) or path.is_symlink(): continue
            try:
                resolved=path.resolve()
                if resolved != repo and repo not in resolved.parents: continue
            except Exception: continue
            archive.add(path, arcname=rel, recursive=False)
    content = temp.read_bytes(); digest=hashlib.sha256(content).hexdigest()
    return {"ok":True,"sha256":digest,"size_bytes":len(content),"build_steps":build_results,"test_steps":test_results}, temp


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            target=(destination/member.name).resolve()
            if target != root and root not in target.parents: raise RuntimeError("archive path traversal detected")
            if member.issym() or member.islnk():
                # Release artifacts do not need arbitrary links; reject them rather than trusting link targets.
                raise RuntimeError("archive contains symbolic/hard link")
        archive.extractall(destination, filter="data")


def _service_allowed(name: str | None) -> str:
    if not name or not SERVICE_RE.fullmatch(name): raise RuntimeError("invalid service name")
    if not ALLOWED_SERVICES:
        raise RuntimeError("service operations are disabled until AIOC_AGENT_ALLOWED_SERVICES is configured")
    if not any(name == prefix or name.startswith(prefix) for prefix in ALLOWED_SERVICES):
        raise RuntimeError("service is outside agent allow-list")
    return name


def _service_action(action: str, name: str) -> dict[str, Any]:
    _service_allowed(name)
    if action not in {"status","start","stop","restart"}: raise RuntimeError("unsupported service action")
    argv=["systemctl",action,name]
    return _run(argv, allowed=False, timeout=120)


def _service_logs(payload: dict[str, Any]) -> dict[str, Any]:
    name=_service_allowed(payload.get("service_name")); lines=max(1,min(int(payload.get("lines",200)),2000))
    result=_run(["journalctl","-u",name,"-n",str(lines),"--no-pager","--output=short-iso"],allowed=False,timeout=60)
    return {"ok":True,"service_name":name,"lines":lines,"logs":result["stdout"],"stderr":result["stderr"]}


def _container_allowed(name: str | None) -> str:
    if not name or not SERVICE_RE.fullmatch(name): raise RuntimeError("invalid container name")
    if not ALLOWED_CONTAINERS:
        raise RuntimeError("docker container operations are disabled until AIOC_AGENT_ALLOWED_CONTAINERS is configured")
    if not any(name == prefix or name.startswith(prefix) for prefix in ALLOWED_CONTAINERS):
        raise RuntimeError("container is outside agent allow-list")
    return name


def _docker_list() -> dict[str, Any]:
    if not ALLOWED_CONTAINERS:
        raise RuntimeError("docker inspection is disabled until AIOC_AGENT_ALLOWED_CONTAINERS is configured")
    result=_run(["docker","ps","--format","{{json .}}"],allowed=False,timeout=60)
    containers=[]
    for line in result["stdout"].splitlines():
        try: item=json.loads(line)
        except Exception: continue
        name=str(item.get("Names") or "")
        if any(name == prefix or name.startswith(prefix) for prefix in ALLOWED_CONTAINERS): containers.append(item)
    return {"ok":True,"containers":containers,"count":len(containers)}


def _docker_logs(payload: dict[str, Any]) -> dict[str, Any]:
    name=_container_allowed(payload.get("container_name")); lines=max(1,min(int(payload.get("lines",200)),2000))
    result=_run(["docker","logs","--tail",str(lines),name],allowed=False,timeout=60)
    return {"ok":True,"container_name":name,"lines":lines,"logs":result["stdout"],"stderr":result["stderr"]}


def _docker_restart(payload: dict[str, Any]) -> dict[str, Any]:
    name=_container_allowed(payload.get("container_name"))
    result=_run(["docker","restart",name],allowed=False,timeout=120)
    return {"ok":True,"container_name":name,"stdout":result["stdout"],"stderr":result["stderr"]}


def _deploy_stage(payload: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    target_root = _safe_path(str(payload["target_root"]), must_exist=False)
    # Parent must be within an allowed root even before target_root exists.
    _safe_path(str(target_root.parent), must_exist=True)
    releases = target_root / "releases"; releases.mkdir(parents=True, exist_ok=True)
    release_path = releases / str(payload["release_id"])
    if release_path.exists(): shutil.rmtree(release_path)
    release_path.mkdir(parents=True)
    _safe_extract(artifact_path, release_path)
    return {"ok":True,"release_path":str(release_path),"files":sum(1 for p in release_path.rglob("*") if p.is_file())}


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

def _safe_relative_child(root: Path, relative: str) -> Path:
    rel=Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise RuntimeError("unsafe relative deployment path")
    target=(root/rel).resolve(strict=False)
    resolved_root=root.resolve(strict=True)
    if target != resolved_root and resolved_root not in target.parents:
        raise RuntimeError("deployment path escapes target root")
    return target

def _dotenv_value(value: str) -> str:
    if "\x00" in value:
        raise RuntimeError("environment value contains NUL")
    return json.dumps(value, ensure_ascii=False)

async def _deploy_configure(client: "AgentClient", payload: dict[str, Any]) -> dict[str, Any]:
    target_root=_safe_path(str(payload["target_root"]),must_exist=True)
    relative=str(payload.get("env_relative_path") or "shared/.env")
    env_path=_safe_relative_child(target_root,relative)
    environment=dict(payload.get("environment") or {})
    secret_env=dict(payload.get("secret_env") or {})
    values: dict[str,str]={}
    for key,value in environment.items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            raise RuntimeError(f"invalid environment variable name: {key}")
        values[str(key)]=str(value)
    fetched=[]
    for key,name in secret_env.items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            raise RuntimeError(f"invalid secret environment variable name: {key}")
        secret=await client.secret(str(name))
        values[str(key)]=secret
        fetched.append(str(name))
    env_path.parent.mkdir(parents=True,exist_ok=True)
    # Reject symlink destinations so a compromised target cannot redirect secret writes.
    if env_path.is_symlink():
        raise RuntimeError("deployment env file cannot be a symlink")
    temp=env_path.with_name(env_path.name+f".tmp-{os.getpid()}")
    lines=[f"{key}={_dotenv_value(value)}" for key,value in sorted(values.items())]
    temp.write_text("\n".join(lines)+("\n" if lines else ""),encoding="utf-8")
    os.chmod(temp,0o600)
    os.replace(temp,env_path)
    os.chmod(env_path,0o600)
    return {"ok":True,"env_file":str(env_path),"keys":sorted(values),"secret_refs":sorted(fetched),"mode":"0600"}

def _activate(payload: dict[str, Any]) -> dict[str, Any]:
    release_path=_safe_path(str(payload["release_path"]))
    target_root=_safe_path(str(payload["target_root"]), must_exist=True)
    env_file=payload.get("env_file")
    if env_file and payload.get("link_env_to_release",True):
        env_path=_safe_path(str(env_file),must_exist=True)
        if env_path != target_root and target_root not in env_path.parents:
            raise RuntimeError("deployment env file is outside target root")
        release_env=release_path/".env"
        if release_env.exists() or release_env.is_symlink():
            raise RuntimeError("release already contains .env; refusing managed secret link overwrite")
        release_env.symlink_to(env_path)
    current=target_root/"current"; previous=None
    if current.is_symlink():
        try: previous=str(current.resolve(strict=True))
        except FileNotFoundError: previous=None
    temp=target_root/(".current-"+str(os.getpid()))
    if temp.exists() or temp.is_symlink(): temp.unlink()
    temp.symlink_to(release_path, target_is_directory=True); os.replace(temp,current)
    service_type=payload.get("service_type","none")
    if service_type=="systemd": _service_action("restart",_service_allowed(payload.get("service_name")))
    elif service_type=="docker-compose":
        _run(["docker","compose","up","-d","--remove-orphans"],cwd=release_path,allowed=False,timeout=300)
    elif service_type!="none": raise RuntimeError("unsupported service type")
    return {"ok":True,"current":str(current.resolve()),"previous_release_path":previous}


async def _health(payload: dict[str, Any]) -> dict[str, Any]:
    health_url=payload.get("health_url")
    if health_url:
        parsed=urlparse(str(health_url)); host=(parsed.hostname or "").lower()
        if parsed.scheme not in {"http","https"} or host not in ALLOWED_HEALTH_HOSTS:
            raise RuntimeError("health URL host is not allowed by agent policy")
        async with httpx.AsyncClient(timeout=10) as client:
            response=await client.get(str(health_url))
        return {"ok":response.is_success,"status_code":response.status_code,"body":response.text[:2000]}
    if payload.get("service_type")=="systemd":
        name=_service_allowed(payload.get("service_name")); proc=subprocess.run(["systemctl","is-active",name],text=True,capture_output=True,shell=False)
        return {"ok":proc.returncode==0,"status":proc.stdout.strip(),"stderr":proc.stderr[-2000:]}
    return {"ok":True,"mode":"no-health-check-configured"}


def _deploy_cleanup(payload: dict[str, Any]) -> dict[str, Any]:
    target_root=_safe_path(str(payload["target_root"]),must_exist=True)
    releases=(target_root/"releases").resolve(strict=False)
    if target_root != releases and target_root not in releases.parents:
        raise RuntimeError("release directory escaped target root")
    if not releases.exists():
        return {"ok":True,"deleted":[],"kept":[],"message":"release directory does not exist"}
    keep=max(2,min(int(payload.get("keep",5)),100))
    current_path=None
    current=target_root/"current"
    if current.is_symlink():
        try: current_path=current.resolve(strict=True)
        except FileNotFoundError: current_path=None
    protected=set()
    for value in payload.get("protected_release_paths") or []:
        path=_safe_path(str(value),must_exist=False)
        if releases != path.parent and releases not in path.parents:
            raise RuntimeError("protected release path is outside release directory")
        protected.add(path)
    if current_path: protected.add(current_path)
    dirs=[]
    for path in releases.iterdir():
        if path.is_symlink() or not path.is_dir(): continue
        resolved=path.resolve(strict=True)
        if releases not in resolved.parents: continue
        dirs.append((path.stat().st_mtime,resolved))
    dirs.sort(reverse=True)
    kept=[]; deleted=[]
    for index,(_,path) in enumerate(dirs):
        if path in protected or index < keep:
            kept.append(str(path)); continue
        shutil.rmtree(path)
        deleted.append(str(path))
    return {"ok":True,"keep":keep,"deleted":deleted,"kept":kept}


def _rollback(payload: dict[str, Any]) -> dict[str, Any]:
    previous=_safe_path(str(payload["previous_release_path"]))
    target_root=_safe_path(str(payload["target_root"]),must_exist=True); current=target_root/"current"
    temp=target_root/(".current-rollback-"+str(os.getpid()))
    if temp.exists() or temp.is_symlink(): temp.unlink()
    temp.symlink_to(previous,target_is_directory=True); os.replace(temp,current)
    if payload.get("service_type")=="systemd": _service_action("restart",_service_allowed(payload.get("service_name")))
    elif payload.get("service_type")=="docker-compose": _run(["docker","compose","up","-d","--remove-orphans"],cwd=previous,allowed=False,timeout=300)
    return {"ok":True,"current":str(current.resolve())}


def _publish_nginx(payload: dict[str, Any]) -> dict[str, Any]:
    if not ALLOW_NGINX: raise RuntimeError("nginx publishing is disabled on this agent")
    host=str(payload.get("public_host") or "").lower()
    port=int(payload.get("local_port") or 0)
    if not HOST_RE.fullmatch(host) or not 1<=port<=65535: raise RuntimeError("invalid nginx publishing target")
    NGINX_SITES_DIR.mkdir(parents=True,exist_ok=True)
    config=NGINX_SITES_DIR/f"aioc-{host}.conf"
    body=f'''server {{\n    listen 80;\n    server_name {host};\n    location / {{\n        proxy_pass http://127.0.0.1:{port};\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }}\n}}\n'''
    config.write_text(body)
    _run(["nginx","-t"],allowed=False,timeout=30); _run(["systemctl","reload","nginx"],allowed=False,timeout=60)
    return {"ok":True,"host":host,"port":port,"config":str(config)}


class AgentClient:
    def __init__(self):
        if not TOKEN: raise RuntimeError("AIOC_AGENT_TOKEN is required")
        self.headers={"Authorization":f"Bearer {TOKEN}"}
        self.client=httpx.AsyncClient(base_url=CENTRAL,headers=self.headers,timeout=60)

    async def close(self): await self.client.aclose()

    async def secret(self, name: str) -> str:
        parsed=urlparse(CENTRAL)
        if parsed.scheme != "https" and (parsed.hostname or "").lower() not in {"127.0.0.1","localhost","::1"}:
            raise RuntimeError("deployment secrets require HTTPS when AIOC central is remote")
        response=await self.client.get(f"/api/v1/agent/secrets/{name}")
        response.raise_for_status()
        value=response.json().get("value")
        if not isinstance(value,str) or not value:
            raise RuntimeError("deployment secret is empty or invalid")
        return value

    async def heartbeat(self, status: str="online"):
        response=await self.client.post("/api/v1/agent/heartbeat",json={"version":AGENT_VERSION,"capabilities":CAPABILITIES,"status":status,"metadata":{"hostname":socket.gethostname(),"platform":platform.platform()}}); response.raise_for_status()

    async def claim(self) -> dict[str, Any] | None:
        response=await self.client.post("/api/v1/agent/tasks/claim"); response.raise_for_status(); body=response.json(); return body.get("task")

    async def upload(self, task_id: str, project_id: str, path: Path, sha256: str) -> dict[str, Any]:
        content=path.read_bytes()
        headers={**self.headers,"X-Artifact-SHA256":sha256,"X-Artifact-Filename":path.name,"X-Project-ID":project_id,"Content-Type":"application/octet-stream"}
        response=await self.client.post(f"/api/v1/agent/tasks/{task_id}/artifact",content=content,headers=headers); response.raise_for_status(); return response.json()

    async def download(self, artifact_id: str) -> Path:
        response=await self.client.get(f"/api/v1/agent/artifacts/{artifact_id}"); response.raise_for_status()
        expected=response.headers.get("X-Artifact-SHA256",""); fd,name=tempfile.mkstemp(prefix="aioc-download-",suffix=".tar.gz"); os.close(fd); path=Path(name); path.write_bytes(response.content)
        if expected and hashlib.sha256(response.content).hexdigest()!=expected: path.unlink(missing_ok=True); raise RuntimeError("downloaded artifact checksum mismatch")
        return path

    async def complete(self, task_id: str, status: str, result: dict[str, Any], error: str|None=None):
        response=await self.client.post(f"/api/v1/agent/tasks/{task_id}/complete",json={"status":status,"result":result,"error":error}); response.raise_for_status(); return response.json()


async def execute_task(client: AgentClient, task: dict[str, Any]) -> dict[str, Any]:
    capability=task["capability"]; payload=task.get("payload") or {}
    if capability not in CAPABILITIES: raise RuntimeError("task capability is not enabled on this agent")
    if capability=="system.status": return _system_status()
    if capability=="system.processes": return _process_list(payload)
    if capability=="network.sockets": return _network_sockets(payload)
    if capability=="network.routes": return _network_routes()
    if capability=="firewall.status": return _firewall_status()
    if capability=="package.updates": return _apt_simulation()
    if capability=="package.security_updates": return _security_updates()
    if capability=="package.apply_updates": return _apply_package_updates(payload)
    if capability=="repo.inspect": return _repo_inspect(payload)
    if capability=="repo.context": return _repo_context(payload)
    if capability=="repo.read": return _repo_read(payload)
    if capability=="repo.search": return _repo_search(payload)
    if capability=="repo.apply_patch": return _repo_apply_patch(payload)
    if capability=="repo.run_steps": return _repo_run_steps(payload)
    if capability=="repo.commit": return _repo_commit(payload)
    if capability=="repo.prepare_release":
        result,temp=_package_repo(payload)
        try:
            artifact=await client.upload(task["id"],str(payload.get("project_id") or ""),temp,result["sha256"]); result["artifact_id"]=artifact["id"]; return result
        finally: temp.unlink(missing_ok=True)
    if capability=="deploy.stage":
        temp=await client.download(str(payload["artifact_id"]))
        try: return _deploy_stage(payload,temp)
        finally: temp.unlink(missing_ok=True)
    if capability=="deploy.configure": return await _deploy_configure(client,payload)
    if capability=="deploy.activate": return _activate(payload)
    if capability=="deploy.health": return await _health(payload)
    if capability=="deploy.rollback": return _rollback(payload)
    if capability=="deploy.cleanup": return _deploy_cleanup(payload)
    if capability=="service.status": return _service_action("status",_service_allowed(payload.get("service_name")))
    if capability=="service.start": return _service_action("start",_service_allowed(payload.get("service_name")))
    if capability=="service.stop": return _service_action("stop",_service_allowed(payload.get("service_name")))
    if capability=="service.restart": return _service_action("restart",_service_allowed(payload.get("service_name")))
    if capability=="service.logs": return _service_logs(payload)
    if capability=="docker.list": return _docker_list()
    if capability=="docker.logs": return _docker_logs(payload)
    if capability=="docker.restart": return _docker_restart(payload)
    if capability=="publish.nginx": return _publish_nginx(payload)
    raise RuntimeError(f"unsupported capability: {capability}")


async def main() -> None:
    client=AgentClient(); last_heartbeat=0.0
    try:
        while True:
            now=time.monotonic()
            if now-last_heartbeat>=15:
                await client.heartbeat(); last_heartbeat=now
            task=await client.claim()
            if not task:
                await asyncio.sleep(POLL_SECONDS); continue
            try:
                await client.heartbeat("busy")
                result=await execute_task(client,task)
                await client.complete(task["id"],"success",result)
            except Exception as exc:
                await client.complete(task["id"],"failed",{},str(exc)[:4000])
            finally:
                await client.heartbeat("online"); last_heartbeat=time.monotonic()
    finally: await client.close()


if __name__=="__main__": asyncio.run(main())
