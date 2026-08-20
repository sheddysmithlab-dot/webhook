import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
kv = {
    "AI_ENABLED": "true",
    "AI_API_BASE": "https://api.z.ai/api/paas/v4",
    "AI_MODEL": "glm-4.5-flash",
    "AI_MEDIA_DIR": "/app/data/media",
}


def upsert(src, key, val):
    line = f"{key}={val}"
    if re.search(rf"^{re.escape(key)}=", src, re.M):
        return re.sub(rf"^{re.escape(key)}=.*$", line, src, flags=re.M)
    return src.rstrip() + "\n" + line + "\n"


for key, val in kv.items():
    text = upsert(text, key, val)
if not re.search(r"^AI_API_KEY=", text, re.M):
    text = text.rstrip() + "\nAI_API_KEY=\n"
env_path.write_text(text, encoding="utf-8")
print("ai-env-merged")
