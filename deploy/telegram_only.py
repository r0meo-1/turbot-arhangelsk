"""Restricted bot.py-only deployment; no dependency installation or VK actions."""
import base64,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n",b"\n")).hexdigest()


def dependency_path(raw):
    p=Path(raw)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in raw and (raw in {"requirements.txt","requirements-dev.txt"} or (raw.endswith(".py") and p.parts[0] not in {"tests","scripts","deploy","tools","serverless-demo"} and raw!="bot.py"))


def validate(root,payload):
    if not re.fullmatch(r"[0-9a-f]{40}",payload.get("revision","")):raise ValueError("Invalid revision")
    source=base64.b64decode(payload["source"],validate=True)
    if len(source)>1000000 or digest(source)!=payload["sha256"]:raise ValueError("Source hash mismatch")
    compile(source,"bot.py","exec")
    deps=payload["dependencies"]
    required={"requirements.txt","shared/alean.py","shared/version.py","vk_bot.py"}
    if not required.issubset(deps):raise ValueError("Incomplete dependency manifest")
    for raw,expected in deps.items():
        path=root/raw
        if not dependency_path(raw) or path.is_symlink() or digest(path.read_bytes())!=expected:
            raise ValueError("Runtime dependency mismatch: "+raw)
    return source


def deploy(root,state,payload,run=subprocess.run,check=None):
    source=validate(root,payload);target=root/"bot.py";revision=root/".deployed-commit"
    if target.is_symlink() or revision.is_symlink():raise ValueError("Refusing symlink")
    if digest(target.read_bytes())==payload["sha256"] and revision.exists() and revision.read_text().strip()==payload["revision"]:
        if check:check(payload["revision"])
        return "already_current"
    original=target.read_bytes();old_revision=revision.read_bytes() if revision.exists() else None
    state.mkdir(exist_ok=True,mode=0o700)
    (state/"bot.py.previous").write_bytes(original)
    (state/"revision.previous").write_bytes(old_revision or b"")
    stat=target.stat();temp=target.with_name("bot.py.telegram-new")
    def install(data):
        temp.write_bytes(data);os.chmod(temp,stat.st_mode & 0o777);os.chown(temp,stat.st_uid,stat.st_gid);temp.replace(target)
    try:
        install(source)
        revision.write_text(payload["revision"]+"\n");os.chmod(revision,0o644)
        run(["systemctl","restart","turbot.service"],check=True,timeout=90,capture_output=True)
        run(["systemctl","is-active","--quiet","turbot.service"],check=True,timeout=10,capture_output=True)
        if check:check(payload["revision"])
    except Exception:
        install(original)
        if old_revision is None:revision.unlink(missing_ok=True)
        else:revision.write_bytes(old_revision)
        run(["systemctl","restart","turbot.service"],timeout=90,capture_output=True)
        raise
    return "deployed"


def health(expected):
    import requests
    for _ in range(30):
        try:
            r=requests.get("http://127.0.0.1:8000/health",timeout=3)
            if r.ok and r.json().get("revision")==expected[:7]:return
        except (requests.RequestException,ValueError):pass
        time.sleep(1)
    raise RuntimeError("Telegram health revision mismatch")


def main():
    import fcntl
    os.umask(0o077)
    if not Path("/etc/turbot/vk-moderation-freeze").is_file():raise RuntimeError("VK freeze must be explicit")
    raw=sys.stdin.buffer.read(2000001)
    if len(raw)>2000000:raise ValueError("Oversized deploy payload")
    payload=json.loads(raw)
    state=Path("/var/lib/turbot-telegram-deploy");state.mkdir(exist_ok=True,mode=0o700)
    with (state/"deploy.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=deploy(Path("/opt/turbot"),state,payload,check=health)
    print(json.dumps({"telegram_deploy":result,"revision":payload["revision"],"vk_touched":False}))


if __name__=="__main__":
    try:main()
    except Exception as e:print(json.dumps({"status":"failed","error_type":type(e).__name__}),file=sys.stderr);sys.exit(1)
