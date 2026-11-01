import sys, pathlib
sys.path.insert(0, "tools")
import recall
sid = "session-53767d1e-5461-4653-8ab1-d43de134b9db"
base = pathlib.Path.home()/".dsh"/"sessions"
p = None
for f in base.rglob(sid+"/session.v4.jsonl.zstd"):
    p = f
if p is None:
    print("not found"); sys.exit(1)
meta, msgs = recall.parse_session(p)
out = pathlib.Path("discord-session.txt")
with out.open("w", encoding="utf-8") as fh:
    for idx, role, text in msgs:
        fh.write(f"\n=== [{idx}] {role} ===\n{text}\n")
print("messages:", len(msgs), "->", out, out.stat().st_size, "bytes")
