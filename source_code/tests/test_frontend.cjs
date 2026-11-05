// Contract tests, not a substitute for visual browser inspection.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],{value:'',hidden:false,textContent:'',innerHTML:'',dataset:{},classList:{toggle(){}},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}}]));
for(const [id,value]of Object.entries({executable:'C:/test.exe',rounds:'1',actions:'10',seconds:'60',timeout:'12',interval:'.7',depth:'3',backend:'win32',text:'GUI test',excluded:'Delete',args:'--new\n--test','filter-status':'all'}))elements.get(id).value=value;
const fixture={id:'a'.repeat(12),created:1,status:'stopped',config:{executable:'C:/test.exe',rounds:1,actions:10,depth:3,seconds:60,excluded:'Delete'},event_count:2,runtime:{actions:1,recoveries:1,elapsed:4,message:'用户停止，部分完成',graph:{states:[{id:'root',title:'root',depth:0,controls:2}],edges:[],pending:2},features:{},latest_image:'evidence/00001.png'}};
const records=[{seq:1,time:1,round:1,category:'test',label:'<script>test</script>',status:'executed',reason:'正常操作',after_image:'evidence/00001.png',check:'未验证功能正确性'},
{seq:2,time:2,round:1,category:'recovery',label:'重启',status:'executed',reason:'恢复路径'}];
const posts=[];
const context={console,Number,Math,JSON,Date,Promise,URLSearchParams,history:{replaceState(){}},location:{search:''},setInterval(){},
document:{getElementById(id){assert(elements.has(id),'Missing element '+id);return elements.get(id);},addEventListener(){},querySelectorAll(){return[];}},
fetch:async(url,options)=>{let data;if(options?.method==='POST'){posts.push({url,data:JSON.parse(options.body)});data={id:fixture.id};}else if(url==='/api/session')data={token:'fixture',ready:true,demo_exists:true,demo:'C:/demo.exe',stop_hotkey:'Ctrl+Alt+F12'};else if(url.endsWith('/events?after=0'))data=records;else if(url==='/api/tasks')data=[fixture];else if(url==='/api/tasks/'+fixture.id)data=fixture;else throw Error(url);return{ok:true,json:async()=>data};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,''),context);
(async()=>{
 await vm.runInContext('init()',context);
 assert(elements.get('dependency').textContent.includes('就绪'));
 assert(elements.get('stop-shortcut').textContent.includes('Ctrl+Alt+F12'));
 await vm.runInContext(`openTask('${fixture.id}')`,context);
 assert.equal(elements.get('percent').textContent,'10%');
 assert.equal(elements.get('status').textContent,'已停止');
 assert(elements.get('events-list').innerHTML.includes('&lt;script&gt;'));
 assert(!elements.get('events-list').innerHTML.includes('<script>test'));
 assert(elements.get('events-list').innerHTML.includes('恢复'));
 assert(elements.get('events-list').innerHTML.includes('<details class="recovery-group" data-group="2">'));
 assert(elements.get('events-list').innerHTML.includes('data-event="2"'));
 assert(!elements.get('events-list').innerHTML.includes('<details class="recovery-group" open'));
 context.document.querySelectorAll=()=>[{dataset:{group:'2'}}];
 vm.runInContext('drawEvents()',context);
 assert(elements.get('events-list').innerHTML.includes('data-group="2" open'));
 context.document.querySelectorAll=()=>[];
 assert(elements.get('scope-view').innerHTML.includes('不提供虚构功能覆盖率'));
 assert(!elements.get('report').hidden);
 await vm.runInContext('detail(1)',context);
 assert(elements.get('detail').open);
 assert(elements.get('detail-summary').textContent.includes('未验证功能正确性'));
 elements.get('executable').value='D:/my.exe';
 const config=vm.runInContext('config()',context);
 assert.equal(config.executable,'D:/my.exe');
 assert.deepEqual(Array.from(config.args),['--new','--test']);
 assert.equal(config.backend,'win32');
 assert.equal(config.interaction,'background');
 elements.get('interaction').value='foreground';
 assert.equal(vm.runInContext('config().interaction',context),'foreground');
 assert(!html.includes('zipp'));
 assert(!html.includes('value="random"'));
 console.log('Frontend contracts: EXE/config, full records, partial progress, evidence and escaping OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
