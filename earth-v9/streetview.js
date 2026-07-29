(()=>{'use strict';
function textNode(tag,text,className=''){const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node}
function coordinatesFromPopup(popup){
  const terms=[...popup.querySelectorAll('.popup-grid dt')];
  const term=terms.find(node=>node.textContent.trim().toLowerCase()==='coordinates');
  const value=term?.nextElementSibling?.textContent||'';
  const match=value.match(/(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/);
  if(!match)return null;
  const lat=Number(match[1]),lon=Number(match[2]);
  if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return null;
  return {lat,lon};
}
function addStreetView(popup){
  if(popup.dataset.streetviewReady==='true')return;
  const coords=coordinatesFromPopup(popup);
  if(!coords)return;
  popup.dataset.streetviewReady='true';
  const {lat,lon}=coords;
  const section=document.createElement('section');section.className='popup-streetview';
  section.append(textNode('h4','Interactive Street View','streetview-heading'));
  section.append(textNode('p','Drag inside the panorama to look around. Google displays the nearest available Street View image to the siren coordinates.','streetview-note'));
  const frameWrap=document.createElement('div');frameWrap.className='streetview-embed';
  const iframe=document.createElement('iframe');
  const coordinatePair=`${lat.toFixed(7)},${lon.toFixed(7)}`;
  iframe.src=`https://maps.google.com/maps?q=${encodeURIComponent(coordinatePair)}&layer=c&cbll=${encodeURIComponent(coordinatePair)}&cbp=11,0,0,0,0&source=embed&output=svembed`;
  iframe.title=`Interactive Street View near ${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  iframe.loading='lazy';iframe.referrerPolicy='strict-origin-when-cross-origin';iframe.allowFullscreen=true;
  frameWrap.append(iframe);section.append(frameWrap);
  const actions=document.createElement('div');actions.className='streetview-actions';
  const full=textNode('a','Open full Street View','streetview-link');
  full.href=`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${encodeURIComponent(coordinatePair)}`;full.target='_blank';full.rel='noreferrer';
  const map=textNode('a','Open location in Google Maps','streetview-link');
  map.href=`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(coordinatePair)}`;map.target='_blank';map.rel='noreferrer';
  actions.append(full,map);section.append(actions);
  section.append(textNode('div','If no nearby panorama exists, use the Google Maps link to inspect the satellite location instead.','streetview-status'));
  const videos=popup.querySelector('.popup-videos');
  const anchor=videos||popup.querySelector('.popup-desc')||popup.querySelector('.popup-grid');
  if(anchor)anchor.insertAdjacentElement('afterend',section);else popup.append(section);
}
function scan(root=document){root.querySelectorAll?.('.maplibregl-popup-content').forEach(addStreetView)}
const observer=new MutationObserver(records=>{for(const record of records){for(const node of record.addedNodes){if(node.nodeType!==1)continue;if(node.matches?.('.maplibregl-popup-content'))addStreetView(node);scan(node)}}});
observer.observe(document.body,{childList:true,subtree:true});
scan();
})();
