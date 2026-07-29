#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures as cf
import html, io, json, math, re, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/143 Safari/537.36 SirenFinder/1.0'
TAG_RE=re.compile(r'(?s)<[^>]+>')
SPACE_RE=re.compile(r'\s+')

def clean(v): return SPACE_RE.sub(' ',html.unescape(TAG_RE.sub(' ',v or ''))).strip()

def fetch(url,timeout=120,retries=3,limit=450_000_000):
    last=None
    for i in range(retries):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept':'*/*','Accept-Language':'en-US,en;q=.9'})
            with urlopen(req,timeout=timeout) as r:
                data=r.read(limit+1)
                if len(data)>limit: raise RuntimeError('download too large')
                return data
        except Exception as e:
            last=e
            if i+1<retries: time.sleep(2**i)
    raise RuntimeError(str(last))

def lname(tag): return tag.rsplit('}',1)[-1].lower()

def unpack(data):
    if data[:2]!=b'PK': return [('doc.kml',data)]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(n,z.read(n)) for n in z.namelist() if n.lower().endswith('.kml')]

def child_text(el,tag,default=''):
    for node in el:
        if lname(node.tag)==tag and node.text: return clean(node.text)
    return default

def extended_data(pm):
    out={}
    for el in pm.iter():
        tag=lname(el.tag)
        if tag=='data':
            key=(el.attrib.get('name') or '').strip()
            for c in el:
                if lname(c.tag)=='value' and key and c.text: out[key]=clean(c.text)[:500]
        elif tag=='simpledata':
            key=(el.attrib.get('name') or '').strip()
            if key and el.text: out[key]=clean(el.text)[:500]
    return out

def parse_document(data,source):
    pins=[]; links=[]
    for _,kml in unpack(data):
        root=ET.fromstring(kml)
        def walk(el,folders):
            tag=lname(el.tag); current=folders
            if tag=='folder':
                nm=child_text(el,'name')
                if nm: current=folders+[nm]
            if tag=='networklink':
                for c in el.iter():
                    if lname(c.tag)=='href' and c.text and c.text.strip().startswith('http'):
                        links.append(html.unescape(c.text.strip()))
            if tag=='placemark':
                point=None
                for c in el.iter():
                    if lname(c.tag)=='point': point=c; break
                if point is not None:
                    coord=''
                    for c in point.iter():
                        if lname(c.tag)=='coordinates' and c.text: coord=c.text.strip(); break
                    if coord:
                        try:
                            lon,lat,*_=coord.split()[0].split(','); lon=float(lon); lat=float(lat)
                        except Exception: lon=lat=999
                        if -180<=lon<=180 and -90<=lat<=90:
                            name=child_text(el,'name','Unnamed siren')[:250]
                            desc=child_text(el,'description','')[:1000]
                            pins.append({'type':'Feature','geometry':{'type':'Point','coordinates':[round(lon,7),round(lat,7)]},'properties':{
                                'name':name or 'Unnamed siren','description':desc,'source':source['name'],'sourceUrl':source['url'],
                                'layer':current[-1] if current else '','fields':extended_data(el)
                            }})
            for c in el: walk(c,current)
        walk(root,[])
    return pins,list(dict.fromkeys(links))

def import_source(source):
    mid=source['mapId']; last=None
    for export in [f'https://www.google.com/maps/d/u/0/kml?mid={quote(mid)}&forcekml=1',f'https://www.google.com/maps/d/kml?mid={quote(mid)}&forcekml=1']:
        try:
            pins,links=parse_document(fetch(export),source)
            for link in links[:50]:
                try: pins.extend(parse_document(fetch(urljoin(export,link),limit=250_000_000),source)[0])
                except Exception: pass
            if pins: return {'source':source,'ok':True,'pins':pins}
            last=RuntimeError('no point placemarks')
        except Exception as e: last=e
    return {'source':source,'ok':False,'pins':[],'error':str(last)[:500]}

def haversine(a,b):
    lon1,lat1=a; lon2,lat2=b; r=6371000
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1,math.sqrt(x)))

def dedupe(features,radius=16):
    cell=.0002; buckets={}; out=[]
    for f in features:
        lon,lat=f['geometry']['coordinates']; bx,by=math.floor(lon/cell),math.floor(lat/cell); found=None
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for i in buckets.get((bx+dx,by+dy),[]):
                    if haversine((lon,lat),out[i]['geometry']['coordinates'])<=radius: found=i; break
                if found is not None: break
            if found is not None: break
        if found is None:
            p=f['properties']; p['sources']=[p['source']]; p['sourceUrls']=[p['sourceUrl']]
            out.append(f); buckets.setdefault((bx,by),[]).append(len(out)-1)
        else:
            p=out[found]['properties']; q=f['properties']
            if q['source'] not in p['sources']: p['sources'].append(q['source'])
            if q['sourceUrl'] not in p['sourceUrls']: p['sourceUrls'].append(q['sourceUrl'])
            if len(q.get('name',''))>len(p.get('name','')): p['name']=q['name']
            if len(q.get('description',''))>len(p.get('description','')): p['description']=q['description']
            if not p.get('layer') and q.get('layer'): p['layer']=q['layer']
            fields=dict(p.get('fields') or {})
            for k,v in (q.get('fields') or {}).items(): fields.setdefault(k,v)
            p['fields']=fields
    return out

def main():
    data=Path('data'); data.mkdir(exist_ok=True)
    sources=json.loads((data/'source-registry.json').read_text(encoding='utf-8'))
    results=[]
    with cf.ThreadPoolExecutor(max_workers=7) as pool:
        futures={pool.submit(import_source,s):s for s in sources}
        for future in cf.as_completed(futures):
            result=future.result(); results.append(result)
            print(f"{result['source']['name']}: {len(result['pins']):,}" if result['ok'] else f"{result['source']['name']}: FAILED {result.get('error')}",flush=True)
    raw=[f for r in results for f in r['pins']]
    unique=dedupe(raw)
    now=datetime.now(timezone.utc).isoformat()
    success=sorted([{'name':r['source']['name'],'mapId':r['source']['mapId'],'url':r['source']['url'],'pins':len(r['pins'])} for r in results if r['ok']],key=lambda x:x['name'])
    failed=sorted([{'name':r['source']['name'],'mapId':r['source']['mapId'],'url':r['source']['url'],'error':r.get('error','')} for r in results if not r['ok']],key=lambda x:x['name'])
    geo={'type':'FeatureCollection','metadata':{'updatedAt':now,'rawPinCount':len(raw),'pinCount':len(unique),'sourceCount':len(success),'sourceTotal':len(sources),'failedSourceCount':len(failed)},'features':unique}
    status={'ok':bool(unique),'updatedAt':now,'rawPinCount':len(raw),'pinCount':len(unique),'sourceCount':len(success),'sourceTotal':len(sources),'failedSourceCount':len(failed),'successfulSources':success,'failedSources':failed}
    (data/'pins.geojson').write_text(json.dumps(geo,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (data/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in status.items() if k not in ('successfulSources','failedSources')},indent=2))
    if not unique: raise SystemExit('No pins built')
if __name__=='__main__': main()
