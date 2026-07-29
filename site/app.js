(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const AAD = new TextEncoder().encode('SirenFinder-v1');
  const WORLD = { center: [0, 18], zoom: 1.45 };
  const state = { map: null, records: [], filtered: [], sources: [], failed: [], query: '', source: '', popup: null };

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (n) => Number(n || 0).toLocaleString('en-US');
  function setStatus(text, mode='') { $('#statusText').textContent=text; $('#statusDot').className=`status-dot ${mode}`.trim(); }
  function keyFromHash() { return new URLSearchParams(location.hash.slice(1)).get('key')?.trim() || ''; }
  function hexBytes(hex) {
    if (!/^[0-9a-f]{64}$/i.test(hex)) throw new Error('The access key must contain exactly 64 hexadecimal characters.');
    return Uint8Array.from(hex.match(/../g), (pair) => parseInt(pair,16));
  }
  async function gunzip(bytes) {
    if ('DecompressionStream' in window) {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    }
    if (window.pako) return window.pako.ungzip(bytes);
    throw new Error('This browser cannot decompress the map database.');
  }
  async function decryptDatabase(keyHex) {
    $('#loadingMessage').textContent='Downloading the encrypted worldwide pin database…';
    const response=await fetch('./data/pins.sfg',{cache:'no-store'});
    if(!response.ok) throw new Error(`Pin database download failed (${response.status}).`);
    const file=new Uint8Array(await response.arrayBuffer());
    if(file.length<29) throw new Error('The encrypted pin database is incomplete.');
    const key=await crypto.subtle.importKey('raw',hexBytes(keyHex),{name:'AES-GCM'},false,['decrypt']);
    $('#loadingMessage').textContent='Decrypting and expanding 83,000+ siren records…';
    let compressed;
    try {
      compressed=new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:file.slice(0,12),additionalData:AAD},key,file.slice(12)));
    } catch { throw new Error('That access key is not valid for this SirenFinder database.'); }
    const data=JSON.parse(new TextDecoder().decode(await gunzip(compressed)));
    if(data.v!==1 || !Array.isArray(data.pins) || !data.pins.length) throw new Error('The decrypted pin database is invalid.');
    return data;
  }

  async function createMap() {
    if(!window.maplibregl) throw new Error('The bundled map engine did not load.');
    state.map=new maplibregl.Map({
      container:'map',center:WORLD.center,zoom:WORLD.zoom,minZoom:1,maxZoom:20,attributionControl:true,
      style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[
        {id:'background',type:'background',paint:{'background-color':'#b8cbd2'}},
        {id:'osm',type:'raster',source:'osm',minzoom:2,paint:{'raster-opacity':.9}}
      ]}
    });
    state.map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
    state.map.addControl(new maplibregl.ScaleControl({unit:'imperial'}),'bottom-right');
    await Promise.race([new Promise((resolve)=>state.map.once('load',resolve)),new Promise((resolve)=>setTimeout(resolve,10000))]);
    try {
      const response=await fetch('./vendor/countries-110m.json');
      if(response.ok && window.topojson) {
        const world=await response.json();
        const countries=window.topojson.feature(world,world.objects.countries);
        if(!state.map.getSource('countries')) {
          state.map.addSource('countries',{type:'geojson',data:countries});
          state.map.addLayer({id:'country-fill',type:'fill',source:'countries',paint:{'fill-color':'#dce6e8','fill-opacity':.72}},'osm');
          state.map.addLayer({id:'country-lines',type:'line',source:'countries',paint:{'line-color':'#71858e','line-width':.7,'line-opacity':.9}},'osm');
        }
      }
    } catch(error) { console.warn('Country fallback unavailable',error); }
  }

  function recordFromRow(row) {
    const ids=Array.isArray(row[4])?row[4]:[row[4]];
    const names=ids.map((id)=>state.sources[id]?.[0]||'Unknown source');
    return {sourceIds:ids,search:`${row[2]||''} ${row[3]||''} ${row[5]||''} ${names.join(' ')}`.toLowerCase(),feature:{
      type:'Feature',geometry:{type:'Point',coordinates:[row[0],row[1]]},properties:{name:row[2]||'Unnamed siren',description:row[3]||'',sources:names.join(' · '),sourceIds:JSON.stringify(ids),layer:row[5]||''}
    }};
  }
  function addPinLayers() {
    state.map.addSource('sirens',{type:'geojson',data:{type:'FeatureCollection',features:state.filtered.map((r)=>r.feature)},cluster:true,clusterMaxZoom:14,clusterRadius:52});
    state.map.addLayer({id:'clusters',type:'circle',source:'sirens',filter:['has','point_count'],paint:{
      'circle-color':['step',['get','point_count'],'#278fbd',100,'#1678a8',1000,'#0d5f8d'],
      'circle-radius':['step',['get','point_count'],17,100,22,1000,29],
      'circle-stroke-width':2,'circle-stroke-color':'#fff'
    }});
    state.map.addLayer({id:'pin',type:'circle',source:'sirens',filter:['!', ['has','point_count']],paint:{
      'circle-color':'#f0b429','circle-radius':['interpolate',['linear'],['zoom'],2,3,10,5.5,16,7],
      'circle-stroke-width':1.5,'circle-stroke-color':'#fff'
    }});
    state.map.on('click','clusters',async(event)=>{
      const feature=state.map.queryRenderedFeatures(event.point,{layers:['clusters']})[0];
      if(!feature)return;
      const zoom=await state.map.getSource('sirens').getClusterExpansionZoom(feature.properties.cluster_id);
      state.map.easeTo({center:feature.geometry.coordinates,zoom});
    });
    state.map.on('click','pin',(event)=>showPopup(event.features?.[0]));
    for(const layer of ['clusters','pin']) {
      state.map.on('mouseenter',layer,()=>state.map.getCanvas().style.cursor='pointer');
      state.map.on('mouseleave',layer,()=>state.map.getCanvas().style.cursor='');
    }
  }
  function featureSourceIds(feature) { try{return JSON.parse(feature?.properties?.sourceIds||'[]')}catch{return[]} }
  function showPopup(feature) {
    if(!feature)return;
    const p=feature.properties||{};
    const links=featureSourceIds(feature).map((id)=>{
      const [name,url]=state.sources[id]||['Unknown source',''];
      return url?`<a class="popup-link" href="${esc(url)}" target="_blank" rel="noreferrer">${esc(name)}</a>`:esc(name);
    }).join(' · ');
    state.popup?.remove();
    state.popup=new maplibregl.Popup({maxWidth:'370px'}).setLngLat(feature.geometry.coordinates).setHTML(
      `<h3 class="popup-title">${esc(p.name||'Unnamed siren')}</h3><div class="popup-meta">${esc(p.layer||'')}</div>${p.description?`<div class="popup-description">${esc(p.description)}</div>`:''}<div>${links}</div>`
    ).addTo(state.map);
  }

  function matches(record) { return (!state.source||record.sourceIds.includes(Number(state.source)))&&(!state.query||record.search.includes(state.query.toLowerCase())); }
  function applyFilter(fit=false) {
    state.filtered=state.records.filter(matches);
    state.map?.getSource('sirens')?.setData({type:'FeatureCollection',features:state.filtered.map((r)=>r.feature)});
    $('#resultCount').textContent=fmt(state.filtered.length);
    renderResults();
    if(fit)fitRecords(state.filtered);
  }
  function fitRecords(records) {
    if(!records.length)return;
    if(records.length===1){state.map.flyTo({center:records[0].feature.geometry.coordinates,zoom:15});showPopup(records[0].feature);return;}
    const bounds=new maplibregl.LngLatBounds();records.forEach((r)=>bounds.extend(r.feature.geometry.coordinates));
    state.map.fitBounds(bounds,{padding:55,maxZoom:13,duration:650});
  }
  function renderResults() {
    const box=$('#results');box.replaceChildren();
    const list=(state.query||state.source?state.filtered:state.filtered.slice(0,80)).slice(0,200);
    if(!list.length){box.innerHTML='<div class="empty">No loaded sirens match.</div>';return;}
    const fragment=document.createDocumentFragment();
    for(const record of list){
      const button=document.createElement('button');button.type='button';button.className='result';
      button.innerHTML=`<strong>${esc(record.feature.properties.name)}</strong><small>${esc(record.feature.properties.sources)}</small>`;
      button.addEventListener('click',()=>{state.map.flyTo({center:record.feature.geometry.coordinates,zoom:15});showPopup(record.feature);if(matchMedia('(max-width:760px)').matches)toggleSidebar(false)});
      fragment.append(button);
    }
    box.append(fragment);
  }
  function buildSourceFilter() {
    const counts=new Map();state.records.forEach((r)=>r.sourceIds.forEach((id)=>counts.set(id,(counts.get(id)||0)+1)));
    const select=$('#sourceFilter');select.innerHTML='<option value="">All loaded source maps</option>';
    [...counts].sort((a,b)=>(state.sources[a[0]]?.[0]||'').localeCompare(state.sources[b[0]]?.[0]||'')).forEach(([id,count])=>{
      const option=document.createElement('option');option.value=String(id);option.textContent=`${state.sources[id]?.[0]||'Unknown'} (${fmt(count)})`;select.append(option);
    });
  }
  function renderFailures() {
    $('#failedCount').textContent=fmt(state.failed.length);const box=$('#failedMaps');box.replaceChildren();
    if(!state.failed.length){box.innerHTML='<div class="empty">All recovered sources loaded.</div>';return;}
    for(const [name,url,error] of state.failed){const row=document.createElement('div');row.className='failed-row';row.innerHTML=`<strong>${url?`<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(name)}</a>`:esc(name)}</strong><small>${esc(error)}</small>`;box.append(row)}
  }
  function enableControls(){for(const id of ['search','clearSearch','sourceFilter','fitResults','resetMap'])$(`#${id}`).disabled=false}
  function toggleSidebar(open){$('#sidebar').classList.toggle('open',open);$('#mobilePanel').setAttribute('aria-expanded',String(open))}
  function wireControls(){
    let timer;
    $('#search').addEventListener('input',(e)=>{clearTimeout(timer);timer=setTimeout(()=>{state.query=e.target.value.trim();applyFilter()},130)});
    $('#clearSearch').addEventListener('click',()=>{$('#search').value='';state.query='';applyFilter();$('#search').focus()});
    $('#sourceFilter').addEventListener('change',(e)=>{state.source=e.target.value;applyFilter(Boolean(state.source))});
    $('#fitResults').addEventListener('click',()=>fitRecords(state.filtered));
    $('#resetMap').addEventListener('click',()=>state.map.easeTo(WORLD));
    $('#mobilePanel').addEventListener('click',()=>toggleSidebar(!$('#sidebar').classList.contains('open')));
    $('#retry').addEventListener('click',()=>location.reload());
    $('#unlock').addEventListener('click',()=>{const key=$('#accessKey').value.trim();if(!/^[0-9a-f]{64}$/i.test(key)){$('#accessError').textContent='Enter the complete 64-character key.';return}location.hash=`key=${key}`;start(key)});
    $('#accessKey').addEventListener('keydown',(e)=>{if(e.key==='Enter')$('#unlock').click()});
  }
  async function start(key){
    if(start.running)return;start.running=true;$('#accessCard').classList.add('hidden');$('#loadingCard').classList.remove('hidden');$('#errorCard').classList.add('hidden');setStatus('Decrypting map database…');
    try{
      const data=await decryptDatabase(key);state.sources=data.sources;state.failed=data.failed||[];$('#loadingMessage').textContent='Preparing clusters and search index…';await new Promise((r)=>setTimeout(r,0));
      state.records=data.pins.map(recordFromRow);state.filtered=state.records;await createMap();addPinLayers();buildSourceFilter();renderFailures();renderResults();enableControls();
      const meta=data.meta||{};$('#pinCount').textContent=fmt(state.records.length);$('#sourceCount').textContent=`${fmt(meta.sourceCount)}/${fmt(meta.sourceTotal)}`;$('#updatedAt').textContent=meta.updatedAt?new Date(meta.updatedAt).toLocaleDateString('en-US',{month:'short',day:'numeric'}):'—';$('#resultCount').textContent=fmt(state.records.length);$('#coverage').textContent=`${fmt(meta.sourceCount)} recovered maps loaded; ${fmt(meta.failedSourceCount)} private, deleted, or export-blocked maps are listed below.`;$('#loadingCard').classList.add('hidden');setStatus(`${fmt(state.records.length)} sirens loaded`,'ready');
    }catch(error){console.error(error);$('#loadingCard').classList.add('hidden');if(/access key/i.test(error.message)){$('#accessCard').classList.remove('hidden');$('#accessError').textContent=error.message}else{$('#errorCard').classList.remove('hidden');$('#errorMessage').textContent=error.message}setStatus('Map unavailable','error');start.running=false}
  }
  wireControls();const key=keyFromHash();if(key){$('#accessKey').value=key;start(key)}else{$('#accessCard').classList.remove('hidden');setStatus('Private access required')}
})();
