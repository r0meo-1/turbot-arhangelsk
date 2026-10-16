"""Send the tested main bot.py with a frozen-runtime compatibility manifest."""
import base64,hashlib,json,os,subprocess,sys
from pathlib import Path
from telegram_only import dependency_path,digest


def payload(revision):
    files=subprocess.check_output(["git","ls-files"],text=True).splitlines()
    source=Path("bot.py").read_bytes()
    return {"revision":revision,"source":base64.b64encode(source).decode(),"sha256":digest(source),
            "dependencies":{p:digest(Path(p).read_bytes()) for p in files if dependency_path(p)}}


def main():
    expected=sys.argv[1]
    head=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    remote=subprocess.check_output(["git","ls-remote","origin","refs/heads/main"],text=True).split()[0]
    if expected!=head or expected!=remote:raise RuntimeError("Only current tested main is deployable")
    subprocess.run(["git","diff","--exit-code","HEAD","--"],check=True,capture_output=True)
    raw=b"TURBOT_TELEGRAM_ONLY_V1\n"+json.dumps(payload(expected)).encode()+b"\n"
    subprocess.run(["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","HostKeyAlias=turbot-production","root@"+os.environ["DEPLOY_HOST"],"true"],input=raw,check=True)


if __name__=="__main__":main()
