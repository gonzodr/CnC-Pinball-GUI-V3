import { AfterEffectsBridgeClient } from '../src/bridge-client.js';
import { mkdir, readdir } from 'node:fs/promises';
const client = new AfterEffectsBridgeClient();
const root = 'F:/Projects/cheech and chong/GUI/Videos/2026/Jackpot/PNG_30fps';
await client.call('save_project',{});
for(const amount of [10000,15000,20000,25000,30000,50000,100000]) {
 const comp=amount===100000?'JACKPOT_MASTER':`JACKPOT_${amount}`;
 const folder=`${root}/JACKPOT_${amount}`;
 await mkdir(folder,{recursive:true});
 if((await readdir(folder)).length) throw Error(`Output not empty: ${folder}`);
 const snap=await client.call('get_comp',{comp,max_layers:1});
 if(snap.width!==640 || snap.height!==480 || snap.fps!==30) throw Error(`Unexpected format: ${comp}`);
 const count=Math.round(snap.duration*snap.fps);
 for(let start=0;start<count;start+=25){
  await client.call('render_png_sequence',{comp,output_folder:folder,prefix:`JACKPOT_${amount}`,start_frame:start,end_frame:Math.min(count-1,start+24),digits:5},{timeoutMs:120000});
  console.log(`${amount}: ${Math.min(count,start+25)}/${count}`);
 }
 console.log(`DONE ${folder}`);
}
await client.call('open_comp',{comp:'JACKPOT_50000',time:2.3});
await client.call('save_project',{});
