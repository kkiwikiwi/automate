#!/usr/bin/env python3
import html,io,json,re,time,zipfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote,urljoin
from urllib.request import Request,urlopen
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'; DATA.mkdir(exist_ok=True)
MASTER=['https://www.airraidsirens.net/forums/viewtopic.php?t=27945','https://r.jina.ai/https://www.airraidsirens.net/forums/viewtopic.php?t=27945','https://r.jina.ai/http://www.airraidsirens.net/forums/viewtopic.php?t=27945']
USA='1AA5lVxum02jYm0T3jDepjuNctwVPf51j'; MID=re.compile(r'(?:[?&](?:mid|id)=|/earth/d/|/d/)([\w-]{15,})',re.I); TAG=re.compile(r'<[^>]+>'); A=re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',re.I|re.S); MD=re.compile(r'\[([^]]*)\]\((https?://[^)]+)\)'); URL=re.compile(r'https?://[^\s<>"\']+')
def get(url,limit=300_000_000,tries=4):
 for n in range(tries):
  try:
   r=Request(url,headers={'User-Agent':'Mozilla/5.0 SirenFinder/2.0','Accept':'*/*'})
   with urlopen(r,timeout=120) as x:
    b=x.read(limit+1)
    if len(b)>limit: raise RuntimeError('file too large')
    return b
  except Exception as e:
   if n==tries-1: raise
   time.sleep(2**n)
def clean(s): return re.sub(r'\s+',' ',html.unescape(TAG.sub(' ',s))).strip()
def mid(u):
 m=MID.search(html.unescape(u)); return m.group(1) if m else None
def section(t):
 starts=[t.find(x) for x in ('United States-','United States -','United States') if t.find(x)>=0]
 if not starts:return t
 s=min(starts); ends=[t.find(x,s+500) for x in ('Last edited by','### Re:','Re: Master list','Post Reply') if t.find(x,s+500)>s]
 return t[s:min(ends) if ends else None]
def discover():
 errors=[]; found=[]
 for u in MASTER:
  try:
   t=section(get(u,35_000_000).decode('utf-8','replace')); cand=[]
   for rx in (A,MD):
    for m in rx.finditer(t):
     url=html.unescape(m.group(1) if m.group(1).startswith('http') else m.group(2)); label=m.group(2) if m.group(1).startswith('http') else m.group(1)
     if mid(url): cand.append((m.start(),url,label))
   for m in URL.finditer(t):
    url=html.unescape(m.group(0).rstrip('.,);]'))
    if mid(url):cand.append((m.start(),url,''))
   seen=set(); out=[]
   for pos,url,label in sorted(cand):
    i=mid(url)
    if not i or i in seen:continue
    seen.add(i); line=clean(t[t.rfind('\n',0,pos)+1:pos]); name=(line.rsplit(':',1)[0] if ':' in line else clean(label)) or f'Map {len(out)+1}'; name=name.strip(' -*#>:|')[:100]
    out.append({'name':name,'mapId':i,'url':url,'directory':True})
   if len(out)>=40: found=out; break
   errors.append(f'{u}: only {len(out)} maps')
  except Exception as e: errors.append(f'{u}: {e}')
 reg=DATA/'source-registry.json'
 if reg.exists():
  try: found+=json.loads(reg.read_text())
  except: pass
 found.append({'name':'Official U.S.A. Siren Map','mapId':USA,'url':f'https://www.google.com/maps/d/viewer?mid={USA}','directory':False})
 unique={}
 for s in found:
  if s.get('mapId'): unique[s['mapId']]=s
 return sorted(unique.values(),key=lambda x:(not x.get('directory',True),x['name'].lower())),errors
def local(tag):return tag.rsplit('}',1)[-1].lower()
def parse(data,src):
 if data[:2]==b'PK':
  pins=[];links=[]
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   for n in z.namelist():
    if n.lower().endswith('.kml'):
     p,l=parse(z.read(n),src);pins+=p;links+=l
  return pins,links
 root=ET.fromstring(data); pins=[];links=[]
 for e in root.iter():
  if local(e.tag)=='networklink':
   for c in e.iter():
    if local(c.tag)=='href' and (c.text or '').strip().startswith('http'):links.append(html.unescape(c.text.strip()))
  if local(e.tag)!='placemark':continue
  name='Unnamed siren';desc='';coords='';layer=''
  for c in e.iter():
   t=(c.text or '').strip(); k=local(c.tag)
   if k=='name' and t and name=='Unnamed siren':name=clean(t)[:220]
   elif k=='description' and t and not desc:desc=clean(t)[:600]
   elif k=='coordinates' and t and not coords:coords=t
  if not coords:continue
  try: lon,lat=map(float,coords.split()[0].split(',')[:2])
  except:continue
  if not(-180<=lon<=180 and -90<=lat<=90):continue
  pins.append({'type':'Feature','geometry':{'type':'Point','coordinates':[round(lon,7),round(lat,7)]},'properties':{'name':name,'description':desc,'source':src['name'],'sourceUrl':src['url']}})
 return pins,list(dict.fromkeys(links))
def import_map(src):
 last=None
 for u in (f'https://www.google.com/maps/d/u/0/kml?mid={quote(src["mapId"])}&forcekml=1',f'https://www.google.com/maps/d/kml?mid={quote(src["mapId"])}&forcekml=1'):
  try:
   pins,links=parse(get(u),src)
   for link in links[:50]:
    try:p,_=parse(get(urljoin(u,link),240_000_000),src);pins+=p
    except Exception as e:print(' network link failed',e)
   if pins:return pins
  except Exception as e:last=e
 raise RuntimeError(last or 'no point placemarks')
def merge(allpins):
 out={}
 for f in allpins:
  lon,lat=f['geometry']['coordinates']; k=(round(lon,5),round(lat,5)); p=f['properties']
  if k not in out:
   p['sources']=[p['source']];p['sourceUrls']=[p['sourceUrl']];out[k]=f
  else:
   q=out[k]['properties']
   if p['source'] not in q['sources']:q['sources'].append(p['source'])
   if p['sourceUrl'] not in q['sourceUrls']:q['sourceUrls'].append(p['sourceUrl'])
   if len(p.get('name',''))>len(q.get('name','')):q['name']=p['name']
   if len(p.get('description',''))>len(q.get('description','')):q['description']=p['description']
 return list(out.values())
def main():
 sources,errors=discover(); allpins=[];ok=[];bad=[];print('Sources',len(sources),flush=True)
 for n,s in enumerate(sources,1):
  try:
   p=import_map(s);allpins+=p;ok.append({**s,'pins':len(p)});print(n,s['name'],len(p),flush=True)
  except Exception as e:bad.append({**s,'error':str(e)});print(n,s['name'],'FAILED',e,flush=True)
 pins=merge(allpins);now=datetime.now(timezone.utc).isoformat();dt=sum(bool(s.get('directory')) for s in sources);ds=sum(bool(s.get('directory')) for s in ok)
 meta={'updatedAt':now,'pinCount':len(pins),'sourceCount':len(ok),'directorySourceCount':ds,'directorySourceTotal':dt,'failedSourceCount':len(bad)}
 geo={'type':'FeatureCollection','metadata':meta,'features':pins}; st={'ok':bool(pins),**meta,'message':f'Loaded {len(pins):,} unique sirens from {len(ok)} maps ({ds}/{dt} master-directory maps).' if pins else 'No public KML pin data could be retrieved.','discoveryErrors':errors,'successfulSources':ok,'failedSources':bad}
 (DATA/'pins.geojson').write_text(json.dumps(geo,ensure_ascii=False,separators=(',',':')));(DATA/'status.json').write_text(json.dumps(st,ensure_ascii=False,indent=2));(DATA/'source-registry.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2));print(st['message']);return 0 if pins else 1
if __name__=='__main__':raise SystemExit(main())
