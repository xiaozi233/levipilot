import re,sys
path=r'.\reverse\ida_out\pseudocode.c'
txt=open(path,encoding='utf-8',errors='replace').read()
blocks={}
for m in re.finditer(r'/\* ===== (.*?) @ ([0-9A-F]+) size=(\d+) ===== \*/',txt):
    start=m.end(); nxt=txt.find('\n/* ===== ',start)
    if nxt<0: nxt=len(txt)
    blocks[int(m.group(2),16)]=txt[start:nxt]
for a in sys.argv[1:]:
    v=int(a,16)
    if v in blocks: print(blocks[v])
    else:
        # nearest containing function unknown; print closest
        print(f'/* no exact block {a} */')
