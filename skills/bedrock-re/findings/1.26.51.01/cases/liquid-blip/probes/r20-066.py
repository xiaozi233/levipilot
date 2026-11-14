import json,struct,sys
from pathlib import Path
sys.path.insert(0,'.')
from driver.harness.facade import Harness
root=Path('out/liquid-blip-dynamic-review')
root.mkdir(parents=True,exist_ok=True)
rows=[]
with Harness() as h:
 original=h.state()
 assert original['in_world'] and original['on_ground'] and not original['sneaking']
 assert not any(original['input'][k] for k in ('w','a','s','d','jump','sneak','sprint'))
 assert all(abs(original[k])<1e-6 for k in ('vx','vy','vz'))
 addresses={name:h.client.ecs_context(original['context'],cid) for name,cid in [('state',459100728),('box',0xBAC1B3CF)]}
 def sample(label):
  s=h.state()
  d={k:s[k] for k in ('tick','feet_y','y','head_y','sneaking','on_ground','vy','input')}
  for name,length in [('state',36),('box',32)]:
   raw=h.client.mem_read(addresses[name]['address'],length)
   d[name]=list(struct.unpack('<'+str(length//4)+'f',bytes.fromhex(raw['hex'])))
  d['label']=label;rows.append(d)
 try:
  h.client.input_lock(True)
  sample('standing')
  h.client.set_input(sneak=True,ticks=-1)
  for i in range(5):
   h.client.wait_ticks(4)
   sample('crouching-'+str(i))
  h.client.set_input()
  for i in range(5):
   h.client.wait_ticks(4)
   sample('released-'+str(i))
 finally:
  try:h.client.set_input()
  finally:h.client.input_lock(original['input']['locked'])
 final=h.state()
 report={'method':'dynamic standing/crouching only; API state and component reads are sequential, not a detector entry snapshot','rows':rows,'restored':not final['sneaking'] and not final['input']['sneak'] and final['input']['locked']==original['input']['locked']}
 (root/'crouch-position-check.json').write_text(json.dumps(report,indent=1),encoding='utf-8')
 for row in rows:
  print(json.dumps({k:row[k] for k in ('label','tick','feet_y','y','head_y','sneaking','state','box')}))
 print('RESTORED',report['restored'])
