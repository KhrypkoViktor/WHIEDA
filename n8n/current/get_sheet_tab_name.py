import re
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

for label, sid, gid in [
    ("work", "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4", "1733124410"),
    ("tracker", "1Meop_B58xAxOXt-kPc0zwfsrwBlM8fneMvz0U3m9g8U", "1390269849"),
]:
    url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:json&gid={gid}"
    text = requests.get(url, timeout=30).text
    match = re.search(r'"sheet_name":"([^"]+)"', text) or re.search(r'"name":"([^"]+)"', text)
    print(label, match.group(1) if match else "unknown")
