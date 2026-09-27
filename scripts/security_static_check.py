from __future__ import annotations
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
checks=[
    (re.compile(r"\beval\s*\("),"eval() is forbidden in core"),
    (re.compile(r"\bexec\s*\("),"exec() is forbidden in core"),
    (re.compile(r"subprocess\..*shell\s*=\s*True"),"shell=True is forbidden in core"),
    (re.compile(r"pickle\.loads\s*\("),"pickle.loads() is forbidden in core"),
]
violations=[]
for base in (ROOT/'app', ROOT/'agent'):
  for path in base.rglob('*.py'):
    text=path.read_text(encoding='utf-8')
    for pattern,message in checks:
        for match in pattern.finditer(text): violations.append(f"{path.relative_to(ROOT)}:{text.count(chr(10),0,match.start())+1}: {message}")
if violations:
    print('\n'.join(violations)); sys.exit(1)
print('security-static-check: PASS')
