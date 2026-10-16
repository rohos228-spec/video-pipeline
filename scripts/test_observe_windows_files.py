import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

if os.name != "nt":
    raise SystemExit("Native Windows required; Linux chmod is not this test")
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=False)
namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app/services/observation_files.py"))
folder = root / "bodies"
folder.mkdir()
namespace["secure_directory"](folder)
body = folder / "synthetic.json"
body.write_text(json.dumps({"synthetic":True}), encoding="utf-8")
assert json.loads(body.read_text(encoding="utf-8"))["synthetic"]
(root / "owner-access.json").write_text(json.dumps({"native_windows":True,"current_user_read_write":True,
    "body_sha256":hashlib.sha256(body.read_bytes()).hexdigest(),"alternate_user_token_tested":False}), encoding="utf-8")
print("Native Windows synthetic body read/write: passed; verify saved ACL separately")
