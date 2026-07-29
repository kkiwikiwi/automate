(()=>{'use strict';
const $=s=>document.querySelector(s);
const fmt=n=>new Intl.NumberFormat().format(Number(n)||0);
const state={map:null,manifest:null,searchData:null,searchResults:[],usStarted:false,intlReady:false,usReady:false};
function arrayValue(value){if(Array.isArray(value))return value;if(!value)return[];if(typeof value==='string'){try{const parsed=JSON.parse(value);if(Array.isArray(parsed))return parsed}catch{}return[value]}return[value]}
function sourcesOf(feature){const p=feature.properties||{};const many=arrayValue(p.sources);return many.length?many:arrayValue(p.source)}
function status(text,mode=''){$('#statusText').textContent=text;$('#statusDot').className='dot '+mode}
function progress(text,mode=''){$('#mapProgress').textContent=text;$('#mapProgress').className='map-progress '+mode}
function mapInit(){
  if(!window.maplibregl)throw Error('The map library could not load.');
  state.map=new maplibregl.Map({
    container:'map',center:[8,22],zoom:1.25,minZoom:1,maxZoom:20,dragRotate:false,pitchWithRotate:false,
    style:{version:8,sources:{
      imagery:{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],tileSize:256,attribution:'Satellite imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community'},
      labels:{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],tileSize:256,attribution:'Reference labels: Esri'}
    },layers:[
      {id:'background',type:'background',paint:{'background-color':'#101820'}},
      {id:'satellite',type:'raster',source:'imagery',minzoom:0,maxzoom:20},
      {id:'labels',type:'raster',source:'labels',minzoom:0,maxzoom:20}
    ]}
  });
  state.map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  let rasterErrors=0;
  state.map.on('error',event=>{
    const message=String(event?.error?.message||'');
    if(/raster|tile|image/i.test(message)&&++rasterErrors===8){progress('Satellite tiles are blocked, but siren pins remain usable.','error')}
  });
}
function styleReady(){return new Promise(resolve=>{if(state.map.isStyleLoaded())resolve();else state.map.once('style.load',resolve)})}
function addSirenSource(id,url,color){
  state.map.addSource(id,{type:'geojson',data:url,cluster:true,clusterMaxZoom:13,clusterRadius:48});
  state.map.addLayer({id:`${id}-clusters`,type:'circle',source:id,filter:['has','point_count'],paint:{
    'circle-color':color,'circle-opacity':.94,
    'circle-radius':['step',['get','point_count'],17,50,21,250,25,1000,30],
    'circle-stroke-width':2.2,'circle-stroke-color':'#fff'
  }});
  state.map.addLayer({id:`${id}-pins`,type:'circle',source:id,filter:['!',['has','point_count']],paint:{
    'circle-color':color,'circle-radius':['interpolate',['linear'],['zoom'],2,4,9,6,15,8],
    'circle-stroke-width':2,'circle-stroke-color':'#fff'
  }});
  state.map.on('click',`${id}-clusters`,event=>{const feature=event.features?.[0];if(feature)clusterPopup(id,feature,event.lngLat).catch(console.error)});
  state.map.on('click',`${id}-pins`,event=>{const feature=event.features?.[0];if(feature)detailPopup(feature,event.lngLat)});
  [`${id}-clusters`,`${id}-pins`].forEach(layer=>{
    state.map.on('mouseenter',layer,()=>state.map.getCanvas().style.cursor='pointer');
    state.map.on('mouseleave',layer,()=>state.map.getCanvas().style.cursor='');
  });
}
function textNode(tag,text,className=''){const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node}
function unknown(){return textNode('span','Not provided by source','popup-unknown')}
function detailPopup(feature,lngLat=feature.geometry.coordinates){
  const p=feature.properties||{},wrap=document.createElement('div');
  wrap.append(textNode('h3',p.name||'Unnamed siren','popup-title'));
  wrap.append(textNode('div',sourcesOf(feature).slice(0,6).join(' · '),'popup-source'));
  const dl=document.createElement('dl');dl.className='popup-grid';
  const rows=[['Siren type',p.sirenType],['Testing schedule',p.testingSchedule],['Coordinates',`${Number(feature.geometry.coordinates[1]).toFixed(6)}, ${Number(feature.geometry.coordinates[0]).toFixed(6)}`]];
  rows.forEach(([label,value])=>{const dt=textNode('dt',label),dd=document.createElement('dd');if(value)dd.textContent=value;else dd.append(unknown());dl.append(dt,dd)});
  wrap.append(dl);
  if(p.description)wrap.append(textNode('div',p.description,'popup-desc'));
  const urls=arrayValue(p.sourceUrls).length?arrayValue(p.sourceUrls):arrayValue(p.sourceUrl);
  if(urls[0]){const link=textNode('a','Open original source','popup-link');link.href=urls[0];link.target='_blank';link.rel='noreferrer';wrap.append(link)}
  new maplibregl.Popup({maxWidth:'430px'}).setLngLat(lngLat).setDOMContent(wrap).addTo(state.map);
}
async function clusterPopup(sourceId,feature,lngLat){
  const source=state.map.getSource(sourceId),count=Number(feature.properties.point_count)||0;
  const leaves=await source.getClusterLeaves(Number(feature.properties.cluster_id),Math.min(count,15),0);
  const wrap=document.createElement('div');wrap.append(textNode('h3',`${fmt(count)} sirens in this group`,'popup-title'));
  wrap.append(textNode('p','Choose a siren below for its type and testing schedule. This group does not zoom automatically.','cluster-note'));
  const list=document.createElement('div');list.className='cluster-list';let popup;
  leaves.forEach(leaf=>{
    const p=leaf.properties||{},button=document.createElement('button');button.type='button';button.className='cluster-item';
    button.append(textNode('strong',p.name||'Unnamed siren'),textNode('small',p.sirenType||p.testingSchedule||sourcesOf(leaf).slice(0,2).join(' · ')||'Details unavailable'));
    button.addEventListener('click',()=>{popup?.remove();detailPopup(leaf,leaf.geometry.coordinates)});list.append(button);
  });
  wrap.append(list);
  if(count>leaves.length)wrap.append(textNode('div',`Showing ${leaves.length} of ${fmt(count)} sirens.`,'cluster-note'));
  const actions=document.createElement('div');actions.className='cluster-actions';
  const zoom=textNode('button','Zoom to separate group','zoom-button');zoom.type='button';
  zoom.addEventListener('click',async()=>{const level=await source.getClusterExpansionZoom(Number(feature.properties.cluster_id));state.map.easeTo({center:feature.geometry.coordinates,zoom:Math.min(level,17)});popup?.remove()});
  actions.append(zoom);wrap.append(actions);
  popup=new maplibregl.Popup({maxWidth:'450px'}).setLngLat(lngLat).setDOMContent(wrap).addTo(state.map);
}
function watchSource(id,onReady){
  const check=()=>{try{if(state.map.isSourceLoaded(id)){state.map.off('sourcedata',handler);onReady();return true}}catch{}return false};
  const handler=event=>{if(event.sourceId===id)check()};state.map.on('sourcedata',handler);check();
}
function startUs(){if(state.usStarted)return;state.usStarted=true;progress('International pins ready · streaming U.S. pins in background…');addSirenSource('usSirens','../data/pins-us.geojson','#f0b429');watchSource('usSirens',()=>{state.usReady=true;progress('All 65,095 sirens ready.','ready');status('65,095 sirens ready','ready');setTimeout(()=>$('#mapProgress').classList.add('hidden'),1800)})}
async function readJson(url){const response=await fetch(url);if(!response.ok)throw Error(`${response.status} ${response.statusText}`);return response.json()}
async function loadManifest(){try{const manifest=await readJson('../data/manifest.json');state.manifest=manifest;$('#totalCount').textContent=fmt(manifest.total);$('#internationalCount').textContent=fmt(manifest.international);$('#usCount').textContent=fmt(manifest.us);if(manifest.updatedAt)$('#updatedAt').textContent=new Date(manifest.updatedAt).toLocaleDateString('en-US',{month:'short',day:'numeric'})}catch(error){console.warn('Manifest unavailable',error)}}
async function ensureSearchData(){
  if(state.searchData)return state.searchData;
  status('Loading searchable index…');$('#startupNote').textContent='Loading the searchable index because search was used. The map itself remains interactive.';
  const [world,us]=await Promise.all([readJson('../data/pins-world.geojson'),readJson('../data/pins-us.geojson')]);
  state.searchData=[...(world.features||[]),...(us.features||[])];$('#startupNote').textContent='Search index ready. Future searches reuse the browser cache.';return state.searchData;
}
function renderResults(features){
  state.searchResults=features;const box=$('#results');box.replaceChildren();$('#resultCount').textContent=fmt(features.length);
  if(!features.length){box.append(textNode('div','No matching sirens found.','empty'));return}
  const fragment=document.createDocumentFragment();features.slice(0,200).forEach(feature=>{
    const p=feature.properties||{},button=document.createElement('button');button.type='button';button.className='result';
    button.append(textNode('strong',p.name||'Unnamed siren'),textNode('small',p.sirenType||p.testingSchedule||sourcesOf(feature).slice(0,2).join(' · ')));
    button.addEventListener('click',()=>{state.map.flyTo({center:feature.geometry.coordinates,zoom:15});detailPopup(feature);if(matchMedia('(max-width:760px)').matches)$('#sidebar').classList.remove('open')});fragment.append(button);
  });box.append(fragment);
}
async function runSearch(){
  const query=$('#search').value.trim().toLowerCase();if(!query){state.searchResults=[];$('#resultCount').textContent='0';$('#results').innerHTML='<div class="empty">Type above to search the database.</div>';return}
  try{const data=await ensureSearchData();const matches=data.filter(feature=>{const p=feature.properties||{};return `${p.name||''} ${p.sirenType||''} ${p.testingSchedule||''} ${p.description||''} ${sourcesOf(feature).join(' ')}`.toLowerCase().includes(query)});renderResults(matches);status(`${fmt(matches.length)} search matches`,'ready')}catch(error){console.error(error);status('Search index failed','error');$('#startupNote').textContent=`Search index failed: ${error.message}`}
}
function fitFeatures(features){if(!features.length)return;if(features.length===1){state.map.flyTo({center:features[0].geometry.coordinates,zoom:15});return}const bounds=new maplibregl.LngLatBounds;features.forEach(feature=>bounds.extend(feature.geometry.coordinates));state.map.fitBounds(bounds,{padding:55,maxZoom:12})}
async function start(){
  try{
    mapInit();loadManifest();
    await styleReady();
    progress('Loading 7,481 international sirens…');
    addSirenSource('intlSirens','../data/pins-world.geojson','#28b9e7');
    watchSource('intlSirens',()=>{
      if(state.intlReady)return;state.intlReady=true;progress('7,481 international sirens ready.','ready');status('International sirens ready','ready');$('#startupNote').textContent='International pins are visible. The U.S. file is now loading separately in the background.';startUs();
    });
    setTimeout(startUs,2500);
  }catch(error){console.error(error);progress(`Map failed: ${error.message}`,'error');status('Map failed','error')}
}
let timer;$('#search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(runSearch,250)});$('#clear').addEventListener('click',()=>{$('#search').value='';runSearch()});$('#fitSearch').addEventListener('click',()=>fitFeatures(state.searchResults));$('#earth').addEventListener('click',()=>state.map.easeTo({center:[8,22],zoom:1.25}));$('#mobilePanel').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));start();
})();
