"""Create an isolated synthetic traffic fixture without changing original data."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import collections,json,argparse,shutil
ap=argparse.ArgumentParser()
ap.add_argument("--pack",required=True);ap.add_argument("--out",required=True)
args=ap.parse_args()
root=Path(args.pack).resolve()
out=Path(args.out).resolve();out.mkdir(parents=True)
def link_or_copy(source,target):
 try:target.symlink_to(source,target_is_directory=source.is_dir())
 except OSError:
  if source.is_dir():shutil.copytree(source,target)
  else:shutil.copy2(source,target)
for name in ['data','pilot','config','simulator','README.md']:link_or_copy(root/name,out/name)
(out/'replay').mkdir()
for p in (root/'replay').iterdir():
 if p.name!='triggers.jsonl':link_or_copy(p,out/'replay'/p.name)
def dt(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
rows=[json.loads(l) for l in (root/'replay/triggers.jsonl').read_text().splitlines()]
ist=timezone(timedelta(hours=5,minutes=30))
bins=collections.Counter()
for row in rows:
 t=dt(row['deliver_at']).astimezone(ist)
 if 18<=t.hour<22:bins[t.replace(minute=t.minute//10*10,second=0,microsecond=0)]+=1
start,count=max(bins.items(),key=lambda kv:kv[1]);end=start+timedelta(minutes=10)
extra=[]
for e in rows:
 if start<=dt(e['deliver_at'])<end:
  for i in [1,2]:
   cloned=dict(e,event_id=e['event_id']+'-audit-copy-'+str(i),session_id=e['session_id']+'-audit-copy-'+str(i))
   extra.append(cloned)
result=sorted(rows+extra,key=lambda e:e['deliver_at'])
(out/'replay/triggers.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in result))
report={'purpose':'Isolated artificial 3x stress; not valid viewer/impact evidence','start_ist':start.isoformat(),'end_ist':end.isoformat(),
        'original_window_deliveries':count,'tripled_window_deliveries':count*3,'added_deliveries':len(extra),
        'total_deliveries':len(result)}
(out/'stress_manifest.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
