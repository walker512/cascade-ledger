"""Bounded synchronous declared-dependency simulation. No system interaction."""
import csv,hashlib,io,json,os,re,stat
from pathlib import Path
INPUT_LIMIT=1024*1024
OUTPUT_LIMIT=24*1024*1024
class Invalid(ValueError):pass

def read(path,limit=INPUT_LIMIT):
    try:
        if not stat.S_ISREG(Path(path).lstat().st_mode):raise Invalid('Regular non-symlink file required.')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise Invalid('File type or size limit exceeded.')
            with os.fdopen(fd,'rb') as f:fd=None;raw=f.read(limit+1)
        finally:
            if fd is not None:os.close(fd)
        if len(raw)>limit:raise Invalid('File size limit exceeded.')
        raw.decode('utf8');return raw
    except (OSError,UnicodeError):raise Invalid('Cannot safely read bounded UTF-8 file.') from None

def parse(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise Invalid('Duplicate JSON keys.')
            k.encode('utf8');result[k]=v
        return result
    def bad(x):raise Invalid('Non-finite number.')
    try:
        if len(raw)>INPUT_LIMIT:raise Invalid('Input exceeds 1 MiB.')
        result=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=bad)
        queue=[(result,0)]
        while queue:
            value,depth=queue.pop()
            if isinstance(value,(list,dict)):
                if depth>=16:raise Invalid('JSON depth exceeded.')
                queue.extend((v,depth+1) for v in (value.values() if isinstance(value,dict) else value))
            elif isinstance(value,str):value.encode('utf8')
            elif isinstance(value,float):raise Invalid('Only integer ticks are supported.')
        return result
    except (ValueError,UnicodeError,RecursionError):raise Invalid('Invalid bounded JSON.') from None

def fields(item,names):
    if not isinstance(item,dict) or set(item)!=set(names):raise Invalid('Unexpected or missing fields.')
def integer(value,lo,hi):
    if type(value) is not int or not lo<=value<=hi:raise Invalid('Integer outside declared bounds.')
def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,47}',value):raise Invalid('Invalid identifier.')
def load(raw):
    d=parse(raw);fields(d,['schema','ticks','nodes','outages','assertions'])
    integer(d['schema'],1,1);integer(d['ticks'],1,200)
    if not isinstance(d['nodes'],list) or not 1<=len(d['nodes'])<=50:raise Invalid('Expected 1..50 nodes.')
    nodes={};links=0
    for n in d['nodes']:
        fields(n,['id','fail_after','recover_after','groups']);identifier(n['id'])
        if n['id'] in nodes:raise Invalid('Duplicate node.')
        integer(n['fail_after'],1,200);integer(n['recover_after'],1,200)
        if not isinstance(n['groups'],list) or len(n['groups'])>10:raise Invalid('At most 10 groups per node.')
        groups=set()
        for g in n['groups']:
            fields(g,['id','providers','minimum']);identifier(g['id'])
            if g['id'] in groups:raise Invalid('Duplicate group.')
            groups.add(g['id']);providers=g['providers']
            if not isinstance(providers,list) or not 1<=len(providers)<=50:raise Invalid('Expected 1..50 providers.')
            for p in providers:identifier(p)
            if len(set(providers))!=len(providers) or n['id'] in providers:raise Invalid('Duplicate or self provider.')
            integer(g['minimum'],1,len(providers));links+=len(providers)
            if links>500:raise Invalid('At most 500 provider references.')
        nodes[n['id']]=n
    for n in nodes.values():
        for g in n['groups']:
            if not set(g['providers'])<=set(nodes):raise Invalid('Unknown provider.')
    if not isinstance(d['outages'],list) or len(d['outages'])>100:raise Invalid('At most 100 outage intervals.')
    occupied=set()
    for o in d['outages']:
        fields(o,['node','start','end']);identifier(o['node'])
        if o['node'] not in nodes:raise Invalid('Unknown outage node.')
        integer(o['start'],0,d['ticks']-1);integer(o['end'],o['start']+1,d['ticks'])
        for t in range(o['start'],o['end']):
            key=(o['node'],t)
            if key in occupied:raise Invalid('Overlapping outage intervals for one node.')
            occupied.add(key)
    if not isinstance(d['assertions'],list) or len(d['assertions'])>50:raise Invalid('At most 50 assertions.')
    seen=set()
    for a in d['assertions']:
        fields(a,['node','max_down_ticks','up_at_end']);identifier(a['node'])
        if a['node'] not in nodes or a['node'] in seen:raise Invalid('Unknown or repeated assertion node.')
        seen.add(a['node']);integer(a['max_down_ticks'],0,d['ticks'])
        if type(a['up_at_end']) is not bool:raise Invalid('up_at_end must be boolean.')
    return d

def canonical(value):return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf8')

def simulate(model):
    # Public engine consumes validated models. Validate again so direct callers
    # cannot bypass bounds or accidentally feed bools as ticks.
    model=load(canonical(model));nodes={n['id']:n for n in model['nodes']}
    up={n:True for n in nodes};bad={n:0 for n in nodes};good={n:0 for n in nodes}
    forced={(o['node'],t) for o in model['outages'] for t in range(o['start'],o['end'])}
    trace=[];downs={n:0 for n in nodes};first={n:None for n in nodes}
    for tick in range(model['ticks']):
        next_up={}
        for name in sorted(nodes):
            node=nodes[name]
            failed=sorted(g['id'] for g in node['groups'] if sum(up[p] for p in g['providers'])<g['minimum'])
            if (name,tick) in forced:
                state=False;cause='forced';bad[name]=good[name]=0
            elif failed:
                bad[name]+=1;good[name]=0
                state=up[name] and bad[name]<node['fail_after']
                cause='failure_grace' if state else 'dependency'
            else:
                bad[name]=0
                if up[name]:state=True;good[name]=0;cause='healthy'
                else:
                    good[name]+=1;state=good[name]>=node['recover_after']
                    cause='recovered' if state else 'recovery_wait'
            next_up[name]=bool(state)
            if not state:
                downs[name]+=1
                if first[name] is None:first[name]=tick
            trace.append({'tick':tick,'node':name,'up':bool(state),'cause':cause,'failed_groups':failed,'bad_streak':bad[name],'good_streak':good[name]})
        up=next_up
    summary={n:{'down_ticks':downs[n],'first_down_tick':first[n],'up_at_end':up[n]} for n in sorted(nodes)}
    assertions=[]
    for a in sorted(model['assertions'],key=lambda x:x['node']):
        observed=summary[a['node']]
        assertions.append({'node':a['node'],'max_down_ticks':a['max_down_ticks'],'require_up_at_end':a['up_at_end'],'observed_down_ticks':observed['down_ticks'],'observed_up_at_end':observed['up_at_end'],'passed':observed['down_ticks']<=a['max_down_ticks'] and (not a['up_at_end'] or observed['up_at_end'])})
    return {'trace':trace,'nodes':summary,'assertions':assertions,'gate':'no_assertions' if not assertions else 'pass' if all(a['passed'] for a in assertions) else 'fail'}

def compare(left_bytes,right_bytes):
    left,right=load(left_bytes),load(right_bytes)
    # Same challenge, identity and observation window. Structural changes and
    # timers are allowed; neither an easier incident nor weaker assertions are.
    def scenario(m):
        return {'ticks':m['ticks'],'ids':sorted(n['id'] for n in m['nodes']),'outages':sorted(m['outages'],key=lambda o:(o['node'],o['start'],o['end'])),'assertions':sorted(m['assertions'],key=lambda a:a['node'])}
    if canonical(scenario(left))!=canonical(scenario(right)):raise Invalid('Comparison requires identical node IDs, ticks, outage intervals and assertions.')
    a,b=simulate(left),simulate(right)
    changes=[n for n in sorted(a['nodes']) if canonical(next(x for x in left['nodes'] if x['id']==n))!=canonical(next(x for x in right['nodes'] if x['id']==n))]
    return {'schema':1,'semantics':'synchronous-previous-tick-v1','input_sha256':{'left':hashlib.sha256(left_bytes).hexdigest(),'right':hashlib.sha256(right_bytes).hexdigest()},'ticks':left['ticks'],'changed_nodes':changes,'left':a,'right':b,'down_tick_delta_right_minus_left':{n:b['nodes'][n]['down_ticks']-a['nodes'][n]['down_ticks'] for n in sorted(a['nodes'])},'scope':'Declared synthetic model, not measured availability, production validation, causal inference or a deployment recommendation.'}

def timeline(report):
    out=io.StringIO(newline='');writer=csv.writer(out,lineterminator='\n')
    writer.writerow(['variant','tick','node','up','cause','failed_groups','bad_streak','good_streak'])
    for side in ('left','right'):
        for row in report[side]['trace']:writer.writerow([side,row['tick'],row['node'],int(row['up']),row['cause'],';'.join(row['failed_groups']),row['bad_streak'],row['good_streak']])
    return out.getvalue().encode('utf8')

def review(report):
    lines=['# Cascade Ledger comparison','','Declared model only; no live systems were contacted.','',report['scope'],'',f"Left gate: {report['left']['gate']}; right gate: {report['right']['gate']}",'','| Node | Left down ticks | Right down ticks | Delta (right-left) | Right up at end |','|---|---:|---:|---:|---|']
    for n in report['left']['nodes']:
        a,b=report['left']['nodes'][n],report['right']['nodes'][n]
        lines.append(f"| {n} | {a['down_ticks']} | {b['down_ticks']} | {b['down_ticks']-a['down_ticks']} | {b['up_at_end']} |")
    lines+=['','See timeline.csv for every node/tick and report.json for assertions and streaks. A zero down-tick delta does not mean equivalent behavior. No eventual recovery is inferred beyond the finite window. Cycles can remain down after forced outages end.','', 'All nodes start up before tick 0. Dependencies observe the previous tick, while forced outages apply immediately at the current tick. Tick units and timers are assumptions, not measured seconds. The bundle copies both complete models; inspect identifiers before sharing.','']
    return '\n'.join(lines).encode('utf8')

def outputs(report):return {'report.json':canonical(report),'timeline.csv':timeline(report),'review.md':review(report)}
def build(left,right,out):
    raws=[read(left),read(right)];report=compare(*raws);files=outputs(report)
    if any(len(v)>OUTPUT_LIMIT for v in files.values()):raise Invalid('Generated output limit exceeded.')
    files.update(dict(zip(('left.json','right.json'),raws)))
    folder=Path(out);folder.mkdir(exist_ok=False)
    for name,value in files.items():(folder/name).write_bytes(value)
    return report

def verify(folder):
    root=Path(folder)
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()}!={'left.json','right.json','report.json','timeline.csv','review.md'}:raise Invalid('Unexpected bundle structure.')
    report=compare(read(root/'left.json'),read(root/'right.json'))
    for name,expected in outputs(report).items():
        if read(root/name,OUTPUT_LIMIT)!=expected:raise Invalid('Bundle differs from recomputation.')
    return report
