"""Dynamic-only swim-to-liquid-exit comparisons, using published observation sites."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from driver.harness import ModClient
from driver.harness.lab import Builder, settle

X=384.5
VOLUME=(380,-54,339,388,-36,359)

def public(s):
    return {k:v for k,v in s.items() if k not in ('name','uuid')}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--desktop',type=Path,required=True)
    p.add_argument('--runs',default='control,swim-jump,swim-pitch,swim-repeat')
    args=p.parse_args()
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    folder=args.desktop/f'LeviPilot-swim-blip-{stamp}'
    folder.mkdir()
    def save(name,data):
        (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    def emit(fp,data):
        fp.write(json.dumps(data,ensure_ascii=False)+'\n');fp.flush()
    c=ModClient(); c.timeout=8; c.connect_timeout=8
    original=None; built=False; mode_changed=False; owns_record=False; watches=[]; cleanup=[]
    all_runs=[]
    try:
        c.connect(); original=c.state(); status=c.status()
        save('original.json',{'state':public(original),'status':status})
        assert original['in_world'] and original['dimension']==0
        assert not status['record']['active'] and not status['macro_recording'] and not status['macro_playing']
        assert not c.watch_list()['watches'], 'pre-existing watches left untouched'
        assert not original['effects'], 'preserve existing effects rather than clearing'
        c.input_lock(True);c.set_input()
        if original['gamemode']!=0 or original['mayfly'] or original['flying']:
            c.command('gamemode survival');mode_changed=True
            c.wait_ticks(6)
        gate=c.state()
        assert gate['gamemode']==0 and not gate['flying'] and not gate['mayfly'],gate
        c.teleport(X,-60,363.5,yaw=180,pitch=0);c.wait_ticks(15)
        positions=[[x,y,z] for x in range(380,389) for y in range(-54,-35) for z in range(339,360)]
        before=[]
        for i in range(0,len(positions),128):
            before.extend(c.blocks(positions[i:i+128])['blocks'])
        save('area-before.json',before)
        assert len(before)==len(positions) and all(b['name']=='minecraft:air' for b in before),'volume is not empty'
        built=True
        checks=Builder(c).build([
            'fill 380 -54 339 388 -36 359 air',
            'fill 380 -54 339 388 -54 359 stone',
            'fill 380 -53 339 388 -38 339 stone',
            'fill 380 -53 359 388 -47 359 stone',
            'fill 380 -53 339 380 -47 359 stone',
            'fill 388 -53 339 388 -47 359 stone',
            'fill 381 -53 340 387 -47 358 water',
        ],{(384,-54,349):'minecraft:stone',(384,-50,349):'minecraft:water',(384,-47,340):'minecraft:water',
           (384,-46,340):'minecraft:air',(384,-38,339):'minecraft:stone'})
        assert all(x.ok for x in checks),checks
        c.command('effect @s water_breathing 600 0 true')
        c.wait_ticks(10)
        roles=json.loads((ROOT/'docs/roles/1.26.51.01.json').read_text(encoding='utf-8'))
        base=int(c.diag()['module_base'])
        entry=base+int(roles['roles']['@liquid_exit_blip']['address'],0)-int(roles['image_base'],0)
        metadata={'source':'dynamic only; published R-F16/r24-078 entry and +0x146 store; normal HID input',
                  'original':public(original),'surface_y':-46.0,'entry_address':entry,'store_address':entry+0x146,
                  'geometry':c.blocks([[384,y,z] for y in (-54,-53,-50,-47,-46,-38) for z in (339,340,349)]),
                  'liquid_depth':c.liquid_depth([384,-47,340]),'runs':[]}
        save('metadata.json',metadata)
        definitions={
            'control':{'swim':False,'height':-47.7,'z':340.32,'pitch':0,'jump':True,'ticks':55},
            'swim-jump':{'swim':True,'height':-49.4,'z':349.5,'pitch':0,'jump':True,'ticks':85},
            'swim-pitch':{'swim':True,'height':-49.4,'z':349.5,'pitch':-25,'jump':False,'ticks':100},
            'swim-repeat':{'swim':True,'height':-48.8,'z':347.5,'pitch':-15,'jump':True,'ticks':85},
            'swim-steep':{'swim':True,'height':-49.8,'z':347.5,'pitch':-45,'jump':False,'ticks':100},
        }
        for name in args.runs.split(','):
            spec=definitions[name]
            runfolder=folder/name;runfolder.mkdir()
            c.set_input();c.teleport(X,-53,353.5,yaw=180,pitch=0)
            settled=settle(c,ticks=3,timeout=12,on_ground=True,input_sprint=False,include_vertical=True)
            assert settled['ok'],settled
            c.teleport(X,spec['height'],spec['z'],yaw=180,pitch=0)
            c.wait_ticks(5)
            initial=c.state();assert not initial['flying'] and not initial['mayfly']
            cursor=0;watch_cursor=c.watch_list()['head'];seq=0;record_rows=0;watch_lost=0;record_lost=0
            phases=[];states=[];hits=[]
            run={'name':name,'spec':spec,'initial':public(initial),'errors':[],'watches':[]}
            all_runs.append(run)
            record=c.record_bin_start(period=1);owns_record=True
            run['record_started']=record
            with (runfolder/'ticks.jsonl').open('x',encoding='utf-8') as ticksfp, \
                 (runfolder/'states.jsonl').open('x',encoding='utf-8') as statefp, \
                 (runfolder/'watch.jsonl').open('x',encoding='utf-8') as watchfp:
                def drain():
                    nonlocal cursor,watch_cursor,record_rows,watch_lost,record_lost
                    page=c.record_dump(cursor,limit=4096,format='jsonl')
                    assert page['record_id']==record['record_id']
                    record_lost+=page.get('lost',0)
                    for row in page.get('rows',[]):emit(ticksfp,row);record_rows+=1
                    cursor=page['next']
                    page=c.watch_drain(watch_cursor,limit=4096)
                    emit(watchfp,page);watch_cursor=page['next'];watch_lost+=page.get('lost',0)
                    hits.extend(h for h in page['hits'] if h['watch'] in {w['id'] for w in watches})
                def observe():
                    nonlocal seq
                    s=c.state();seq+=1
                    row={'sample':seq,**public(s)};states.append(row);emit(statefp,row)
                    if seq%5==0:drain()
                    return s
                try:
                    watches.append(c.watch_set(entry,size=1,mode='x',thread='both',depth=1,sample=True,tag='swim-lb-entry',
                                               snapshots=[{'reg':'rdx','size':32},{'reg':'r8','size':40},{'reg':'r9','size':36}]))
                    watches.append(c.watch_set(entry+0x146,size=1,mode='x',thread='both',depth=1,sample=True,tag='swim-lb-store'))
                    run['watches']=list(watches)
                    if spec['swim']:
                        c.set_input(forward=1,sprint=True,ticks=140)
                        deadline=time.monotonic()+8
                        consecutive=0
                        while True:
                            s=observe()
                            consecutive=consecutive+1 if s.get('swimming') else 0
                            if consecutive>=3:break
                            if time.monotonic()>deadline:raise RuntimeError('normal HID did not reach sustained swimming')
                        run['swimming_gate']=public(s)
                        phases.append({'name':'swimming_confirmed','tick':s['tick']})
                        print('SWIMMING',name,s['tick'],s['feet_y'],s['z'],flush=True)
                    else:
                        s=observe();assert not s['swimming']
                    c.look(180,spec['pitch'],ticks=1)
                    c.set_input(forward=1,sprint=spec['swim'],jump=spec['jump'],ticks=spec['ticks'])
                    start=c.state()['tick'];phases.append({'name':'approach_and_exit','tick':start})
                    deadline=time.monotonic()+spec['ticks']/20*3+8
                    while True:
                        s=observe()
                        if s['tick']>=start+spec['ticks']+4:break
                        if time.monotonic()>deadline:raise RuntimeError('world did not advance')
                    c.set_input()
                    c.wait_ticks(3)
                except BaseException as exc:
                    run['errors'].append(f'{type(exc).__name__}: {exc}')
                    raise
                finally:
                    for w in watches:
                        try:c.watch_clear(w['id'])
                        except Exception as exc:cleanup.append(str(exc))
                    watches=[]
                    c.set_input()
                    run['stopped']=c.record_bin_stop();owns_record=False
                    drain()
                    run.update(rows=record_rows,record_lost=record_lost,watch_lost=watch_lost,phases=phases,
                               state_samples=len(states),swimming_samples=sum(s['swimming'] for s in states),
                               entries=sum(h['watch']==run['watches'][0]['id'] for h in hits),
                               stores=sum(h['watch']==run['watches'][1]['id'] for h in hits),end=public(c.state()))
                    (runfolder/'run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf-8')
                    save('summary.json',all_runs)
                    print('RUN',json.dumps({k:run[k] for k in ('name','rows','state_samples','swimming_samples','entries','stores','record_lost','watch_lost','errors')}),flush=True)
    finally:
        for w in watches:
            try:c.watch_clear(w['id'])
            except Exception as exc:cleanup.append('watch cleanup: '+str(exc))
        if owns_record:
            try:c.record_bin_stop()
            except Exception as exc:cleanup.append('record cleanup: '+str(exc))
        if original is not None:
            actions=[lambda:c.set_input(),lambda:c.teleport(original['feet_x'],original['feet_y'],original['feet_z'],yaw=original['yaw'],pitch=original['pitch'])]
            if built:actions.append(lambda:c.command('fill 380 -54 339 388 -36 359 air'))
            actions.append(lambda:c.command('effect @s water_breathing 0'))
            if mode_changed:actions.append(lambda:c.command({0:'gamemode survival',1:'gamemode creative',2:'gamemode adventure',6:'gamemode spectator'}[original['gamemode']]))
            actions.append(lambda:c.input_lock(original['input']['locked']))
            for action in actions:
                try:action()
                except Exception as exc:cleanup.append('restore: '+str(exc))
            try:
                c.wait_ticks(8)
                area_after=[]
                if built:
                    for i in range(0,len(positions),128):area_after.extend(c.blocks(positions[i:i+128])['blocks'])
                final=c.state()
                save('integrity.json',{'state':public(final),'watches':c.watch_list(),'status':c.status(),
                                       'area_restored':all(b['name']=='minecraft:air' for b in area_after),
                                       'cleanup_errors':cleanup,'original':public(original)})
            except Exception as exc:cleanup.append('verify: '+str(exc))
        c.close()
        save('completion.json',{'folder':folder.name,'runs':len(all_runs),'cleanup_errors':cleanup,'phase':'stopped'})
        print('SAVED',folder.name,'cleanup_errors',cleanup,flush=True)

if __name__=='__main__':main()
