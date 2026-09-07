import { AfterEffectsBridgeClient } from '../src/bridge-client.js';
const client = new AfterEffectsBridgeClient();
const values = [10000,15000,20000,25000,30000];
const root = 'F:/Projects/cheech and chong/GUI/Videos/2026/Jackpot';
const args = {comp:'JACKPOT_MASTER', variants:values.map(amount=>({amount,name:`JACKPOT_${amount}`,text:amount.toLocaleString('en-US'),path:`${root}/Jackpot_amount_${amount}_v1_rgba.png`}))};
console.log(JSON.stringify(await client.call('duplicate_jackpot_assets',args,{timeoutMs:120000}),null,2));
console.log(JSON.stringify(await client.call('save_project',{})));
for(const amount of values){
 const comp = await client.call('get_comp',{comp:`JACKPOT_${amount}`});
 console.log(JSON.stringify({name:comp.name,bg:comp.layers.filter(l=>l.source?.name==='bg').map(l=>l.source.id),score:comp.layers.filter(l=>l.name.startsWith('SCORE |')).map(l=>({name:l.name,source:l.source,transform:l.transform})),layers:comp.layer_count}));
 console.log(JSON.stringify(await client.call('render_frames',{comp:`JACKPOT_${amount}`,times:[0.8,2.3,4.1]},{timeoutMs:120000})));
}
await client.call('open_comp',{comp:'JACKPOT_30000',time:2.3});
await client.call('save_project',{});
