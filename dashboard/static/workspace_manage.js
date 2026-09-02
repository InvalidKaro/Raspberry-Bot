const $ = id => document.getElementById(id);
let csrf = '';
let resources = [];
let meta = null;
let rows = [];
let selectedRow = null;
let searchTimer = null;

const labels = {
  name:'Name', title:'Titel', body:'Text / Inhalt', color:'Farbe', questions_json:'Fragen',
  content:'Inhalt', category:'Kategorie', source_url:'Material / URL', details:'Details', status:'Status',
  assigned_to:'Zuständig (User-ID)', due_at:'Fällig', starts_at:'Start', channel_id:'Channel-ID',
  event_date:'Datum', start_time:'Uhrzeit', owner_text:'Verantwortliche', kind:'Typ', entry_key:'Key', tags:'Tags',
  question:'Frage', answer:'Antwort', explanation:'Erklärung', message:'Nachricht', user_id:'User-ID',
  delivered:'Zugestellt', achievement_key:'Achievement-Key', description:'Beschreibung', threshold_xp:'XP-Schwelle',
  author_id:'Autor-ID', quote_text:'Zitat', prize:'Gewinn', ends_at:'Ende', winner_id:'Gewinner-ID',
  enabled:'Aktiv', response:'Antwort', run_at:'Ausführen am', repeat_minutes:'Wiederholung (Minuten)',
  payload_json:'Payload', url:'URL', panel_id:'Panel-ID', label:'Beschriftung', action_type:'Aktionstyp', value:'Wert / Definition',
  position:'Position', message_id:'Message-ID', form_id:'Formular-ID', response_json:'Antwortdaten',
  giveaway_id:'Giveaway-ID', role_id:'Rollen-ID'
};

const hints = {
  questions_json:'Fragen mit | trennen oder als JSON-Liste eingeben.',
  payload_json:'Gültiges JSON. Beispiel: {"channel_id":"123","text":"Hallo"}',
  entry_key:'Kurzer eindeutiger Schlüssel, z. B. reanimation.',
  tags:'Kommagetrennte Suchbegriffe.',
  status:'Je nach Modul z. B. open, doing, done oder open/ended.',
  action_type:'Panel: role, link, info oder select-role.',
  value:'Bei RoleSelects ist dies die gespeicherte JSON-Rollenliste; bei Buttons Rollen-ID, URL oder Infotext.',
  run_at:'ISO-Zeitpunkt, z. B. 2026-09-05T20:00.',
  due_at:'ISO-Zeitpunkt, z. B. 2026-09-05T20:00.',
  starts_at:'ISO-Zeitpunkt, z. B. 2026-09-05T20:00.',
  color:'Hex wie #5865F2 oder Integer.',
  source_url:'Optionaler Material-Link.'
};

function note(text, ok=true){
  const el=$('toast');
  el.textContent=text;
  el.className=`show ${ok?'good':'bad'}`;
  clearTimeout(note.timer);
  note.timer=setTimeout(()=>el.className='',4200);
}

async function req(url, opts={}){
  const method=(opts.method||'GET').toUpperCase();
  const headers={'Content-Type':'application/json',...(opts.headers||{})};
  if(!['GET','HEAD','OPTIONS'].includes(method) && csrf) headers['X-CSRF-Token']=csrf;
  const response=await fetch(url,{cache:'no-store',...opts,headers});
  const data=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
  if(!response.ok || data.ok===false) throw new Error(data.message||`HTTP ${response.status}`);
  return data;
}

function esc(value){
  return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function currentResource(){ return resources.find(r=>r.key===$('resourceSelect').value)||null; }
function currentGuild(){ return $('guildSelect').value; }

async function bootstrap(){
  try{
    const boot=await req('/api/bootstrap');
    csrf=boot.csrf||'';
    const [guildData, resourceData]=await Promise.all([
      req('/api/workspace/guilds'),
      req('/api/workspace/manage/resources')
    ]);
    $('guildSelect').innerHTML=(guildData.guilds||[]).map(g=>`<option value="${esc(g)}">${esc(g)}</option>`).join('')||'<option value="">Kein Server gefunden</option>';
    resources=(resourceData.resources||[]).filter(r=>r.available);
    renderResourceOptions();
    if(resources.length){
      $('resourceSelect').value=resources[0].key;
      await loadResource();
    }
  }catch(error){ note(error.message,false); }
}

function renderResourceOptions(){
  const grouped=new Map();
  for(const resource of resources){
    if(!grouped.has(resource.group)) grouped.set(resource.group,[]);
    grouped.get(resource.group).push(resource);
  }
  $('resourceSelect').innerHTML=[...grouped.entries()].map(([group,items])=>
    `<optgroup label="${esc(group)}">${items.map(r=>`<option value="${esc(r.key)}">${esc(r.title)}</option>`).join('')}</optgroup>`
  ).join('');
}

async function loadResource(){
  const resource=currentResource();
  if(!resource)return;
  selectedRow=null;
  $('resourceGroup').textContent=resource.group.toUpperCase();
  $('resourceTitle').textContent=resource.title;
  $('resourceDescription').textContent=resource.description||'';
  try{
    meta=await req(`/api/workspace/manage/${encodeURIComponent(resource.key)}/meta`);
    renderEditor(null);
    await loadRows();
  }catch(error){
    meta=null;
    $('rows').innerHTML=`<div class="empty">${esc(error.message)}</div>`;
    note(error.message,false);
  }
}

async function loadRows(){
  const resource=currentResource();
  if(!resource)return;
  const params=new URLSearchParams();
  if(currentGuild()) params.set('guild_id',currentGuild());
  const q=$('searchInput').value.trim();
  if(q) params.set('q',q);
  try{
    const data=await req(`/api/workspace/manage/${encodeURIComponent(resource.key)}?${params}`);
    rows=data.rows||[];
    $('rowCount').textContent=rows.length;
    renderRows();
  }catch(error){
    rows=[];
    $('rowCount').textContent='0';
    $('rows').innerHTML=`<div class="empty">${esc(error.message)}</div>`;
    note(error.message,false);
  }
}

function rowTitle(row){
  const candidates=['title','name','question','entry_key','message','prize','achievement_key','label','kind'];
  for(const key of candidates){ if(row[key]!==null&&row[key]!==undefined&&String(row[key]).trim()) return String(row[key]); }
  return `Eintrag #${row.__rowid__}`;
}

function rowPreview(row){
  const ignored=new Set(['__rowid__','guild_id','id','created_at','updated_at','created_by']);
  for(const [key,value] of Object.entries(row)){
    if(ignored.has(key)||value===null||value===undefined||String(value).trim()==='')continue;
    if(['title','name','question','entry_key'].includes(key))continue;
    return `${labels[key]||key}: ${String(value).replace(/\s+/g,' ').slice(0,150)}`;
  }
  return 'Keine Vorschau';
}

function renderRows(){
  if(!rows.length){ $('rows').innerHTML='<div class="empty">Keine Einträge gefunden.</div>'; return; }
  $('rows').innerHTML=rows.map((row,index)=>{
    const tags=[];
    if(row.status!==undefined&&row.status!==null)tags.push(String(row.status));
    if(row.category)tags.push(String(row.category));
    if(row.kind)tags.push(String(row.kind));
    if(row.enabled!==undefined&&row.enabled!==null)tags.push(Number(row.enabled)?'aktiv':'aus');
    return `<button type="button" class="row-card${selectedRow&&selectedRow.__rowid__===row.__rowid__?' active':''}" data-row="${index}">
      <div class="row-title">${esc(rowTitle(row))}</div>
      <div class="row-meta"><span class="tag">#${esc(row.__rowid__)}</span>${tags.slice(0,3).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>
      <div class="row-preview">${esc(rowPreview(row))}</div>
    </button>`;
  }).join('');
  document.querySelectorAll('[data-row]').forEach(button=>button.addEventListener('click',()=>{
    selectedRow=rows[Number(button.dataset.row)];
    renderRows();
    renderEditor(selectedRow);
  }));
}

function fieldLabel(field){ return labels[field.name]||field.name.replaceAll('_',' '); }
function isWide(field){ return ['textarea','json'].includes(field.kind)||['body','content','details','description','explanation','message','response','value'].includes(field.name); }

function inputFor(field, value=''){
  const id=`field_${field.name}`;
  const required=field.required?' required':'';
  const help=hints[field.name]?`<span class="field-help">${esc(hints[field.name])}</span>`:'';
  const label=`<span class="field-name"><span>${esc(fieldLabel(field))}${field.required?' <b class="required">*</b>':''}</span><code>${esc(field.name)}</code></span>`;
  let input='';
  const safe=value===null||value===undefined?'':String(value);
  if(field.kind==='boolean'){
    input=`<select id="${id}" data-field="${esc(field.name)}"><option value="0"${Number(value)===0?' selected':''}>Nein / 0</option><option value="1"${Number(value)===1?' selected':''}>Ja / 1</option></select>`;
  }else if(field.kind==='textarea'||field.kind==='json'){
    input=`<textarea id="${id}" data-field="${esc(field.name)}"${required}>${esc(safe)}</textarea>`;
  }else if(field.kind==='datetime'){
    const local=safe ? safe.replace('Z','').slice(0,16) : '';
    input=`<input id="${id}" data-field="${esc(field.name)}" type="datetime-local" value="${esc(local)}"${required}>`;
  }else if(field.kind==='date'){
    input=`<input id="${id}" data-field="${esc(field.name)}" type="date" value="${esc(safe.slice(0,10))}"${required}>`;
  }else if(field.kind==='time'){
    input=`<input id="${id}" data-field="${esc(field.name)}" type="time" value="${esc(safe.slice(0,5))}"${required}>`;
  }else if(field.kind==='integer'||field.kind==='number'){
    input=`<input id="${id}" data-field="${esc(field.name)}" type="number" value="${esc(safe)}"${required}>`;
  }else{
    input=`<input id="${id}" data-field="${esc(field.name)}" type="text" value="${esc(safe)}"${required}>`;
  }
  return `<label class="${isWide(field)?'wide':''}">${label}${input}${help}</label>`;
}

function renderEditor(row){
  selectedRow=row;
  const resource=currentResource();
  if(!meta||!resource){
    $('fields').innerHTML='';
    $('saveButton').disabled=true;
    return;
  }
  const fields=(meta.fields||[]).filter(field=>!field.readonly);
  $('fields').innerHTML=fields.map(field=>inputFor(field,row?row[field.name]:'' )).join('')||'<div class="empty">Keine bearbeitbaren Felder.</div>';
  $('editorTitle').textContent=row?`${resource.title} · #${row.__rowid__}`:`Neuer Eintrag · ${resource.title}`;
  $('saveButton').disabled=row?!resource.can_update:!resource.can_create;
  $('duplicateButton').disabled=!row||!resource.can_create;
  $('deleteButton').disabled=!row||!resource.can_delete;
  $('editorHint').textContent=row
    ? 'Änderungen wirken direkt auf dieselben SQLite-Daten, die der Discord-Bot verwendet.'
    : 'Neuer Eintrag. Server-ID und technische Felder werden automatisch gesetzt.';
}

function collectValues(){
  const values={};
  document.querySelectorAll('[data-field]').forEach(input=>{ values[input.dataset.field]=input.value; });
  return values;
}

async function saveCurrent(event){
  event.preventDefault();
  const resource=currentResource();
  if(!resource||!meta)return;
  const payload={guild_id:currentGuild(),values:collectValues()};
  try{
    if(selectedRow){
      await req(`/api/workspace/manage/${encodeURIComponent(resource.key)}/${selectedRow.__rowid__}`,{method:'PATCH',body:JSON.stringify(payload)});
      note('Eintrag gespeichert.');
    }else{
      await req(`/api/workspace/manage/${encodeURIComponent(resource.key)}`,{method:'POST',body:JSON.stringify(payload)});
      note('Eintrag erstellt.');
    }
    await loadRows();
    renderEditor(null);
  }catch(error){ note(error.message,false); }
}

function duplicateCurrent(){
  if(!selectedRow||!meta)return;
  const copy={...selectedRow};
  selectedRow=null;
  renderEditor(copy);
  selectedRow=null;
  $('editorTitle').textContent=`Duplikat · ${currentResource().title}`;
  $('deleteButton').disabled=true;
  $('duplicateButton').disabled=true;
  $('saveButton').disabled=!currentResource().can_create;
  note('Kopie in den Editor geladen. Eindeutigen Name/Key bei Bedarf ändern.');
}

async function deleteCurrent(){
  const resource=currentResource();
  if(!resource||!selectedRow)return;
  if(!confirm(`Eintrag #${selectedRow.__rowid__} wirklich löschen? Vorher wird automatisch ein SQLite-Backup angelegt.`))return;
  try{
    await req(`/api/workspace/manage/${encodeURIComponent(resource.key)}/${selectedRow.__rowid__}`,{
      method:'DELETE',body:JSON.stringify({guild_id:currentGuild(),confirm:'DELETE'})
    });
    note('Eintrag gelöscht.');
    selectedRow=null;
    renderEditor(null);
    await loadRows();
  }catch(error){ note(error.message,false); }
}

$('resourceSelect').addEventListener('change',loadResource);
$('guildSelect').addEventListener('change',()=>{ selectedRow=null; renderEditor(null); loadRows(); });
$('searchButton').addEventListener('click',loadRows);
$('searchInput').addEventListener('input',()=>{ clearTimeout(searchTimer); searchTimer=setTimeout(loadRows,260); });
$('newButton').addEventListener('click',()=>{ selectedRow=null; renderRows(); renderEditor(null); });
$('resetButton').addEventListener('click',()=>renderEditor(selectedRow));
$('duplicateButton').addEventListener('click',duplicateCurrent);
$('deleteButton').addEventListener('click',deleteCurrent);
$('editorForm').addEventListener('submit',saveCurrent);

bootstrap();
