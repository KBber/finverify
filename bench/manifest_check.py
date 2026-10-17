"""校验重建后的 manifest.json：条数、字节数、修订版、两份问题年报。"""

from __future__ import annotations

import json

import _paths

p = _paths.manifest()
d = json.loads(p.read_text(encoding="utf-8"))

out = [
    f"条数 {len(d)}",
    f"总字节 {sum(m['bytes'] for m in d) / 1024 / 1024:.1f} MB",
    f"修订版 {sum(1 for m in d if m['revised'])} 份",
    "",
]
for m in d:
    if m["sec_code"] in ("688610", "873576"):
        out.append(f"** {m['sec_code']} {m['sec_name']} | {m['title'][:50]}")
out.append("")
out.append("全部:")
for m in d:
    flag = " rev" if m["revised"] else ""
    out.append(
        f"  {m['sec_code']} {m['sec_name']:<10} {m['bytes'] / 1024 / 1024:>5.2f}MB"
        f"{flag} {m['title'][:40]}"
    )

_paths.note("manifest_check.txt").write_text("\n".join(out), encoding="utf-8")
print(f"ok {len(d)}")
