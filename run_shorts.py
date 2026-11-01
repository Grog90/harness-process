import json
import subprocess
import sys

out = subprocess.run(
    [sys.executable, "tools/yt_check.py", "--shorts", "--slides", "5", "--json"],
    capture_output=True, text=True, encoding="utf-8",
)
if not out.stdout.strip():
    print("stderr:", out.stderr[-2000:])
    raise SystemExit(1)

data = json.loads(out.stdout)
result = data[0]
print("ok =", result["ok"])
for slide in result["slides"]:
    slow = slide.get("slow_segments") or []
    print(
        f"slide {slide['index']}: played={slide['played']}s "
        f"buf={slide['buffered']}s segs={slide['segments']} "
        f"avg={slide['seg_avg_ms']}ms max={slide['seg_max_ms']}ms slow={slow}"
    )
