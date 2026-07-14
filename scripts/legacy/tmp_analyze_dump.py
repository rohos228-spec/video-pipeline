import re
from pathlib import Path

p = Path("data/outsee_dumps/video_timeout_20260526_172814.html")
html = p.read_text(encoding="utf-8", errors="replace")
videos = re.findall(r"<video[^>]*>", html, re.I)
print("video tags:", len(videos))
for v in videos[:8]:
    print(" ", v[:250])
ids = re.findall(r"\[ID:\s*([^\]]+)\]", html)
print("ID tokens:", len(ids), ids[:10])
mp4 = re.findall(r'https?://[^\s"<>]+\.mp4[^\s"<>]*', html)
print("mp4 urls:", len(mp4))
for u in mp4[:8]:
    print(" ", u[:140])
poster = re.findall(r'poster="([^"]+)"', html)
print("posters:", len(poster))
for u in poster[:5]:
    print(" ", u[:140])
print("img tags:", len(re.findall(r"<img ", html)))
