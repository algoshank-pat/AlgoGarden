export const businesses = [{id:'casual',name:'Juniper Kitchen',type:'Neighborhood diner'},{id:'fine',name:'Maison Olive',type:'Contemporary fine dining'}];
const definitions = [
 ['Reservation','Booked','Table for four tomorrow at 8 PM.','Your table for four is confirmed for tomorrow at 8 PM.','4 guests · Oct 26 · 8:00 PM',72],
 ['Menu','Answered','Does the roasted cauliflower contain nuts?','It contains almonds. Please also inform staff about your allergy.','Allergen information',43],
 ['Order','Transferred','Can I order two burgers for pickup?','A staff member will take your order. I’ll connect you now.','Food order → staff',18],
 ['Policy','Needs review','Can we bring a birthday cake?','I’m not sure about that policy. Would you like me to take a message or connect you to staff?','No relevant policy found',52],
 ['Hours','Answered','Are you open on Sunday?','We’re open Sunday from 11 AM to 9 PM.','Sunday opening hours',24],
 ['Reservation','Booked','A table for two at 7 PM, please.','Your table for two is confirmed for 7 PM.','2 guests · Oct 25 · 7:00 PM',95],
 ['Human','Transferred','I need to speak to the manager about my visit.','I’ll connect you with a staff member to help.','Complaint → manager',21],
 ['Location','Answered','Where can I park?','There’s complimentary parking behind the restaurant, accessed from Oak Street.','Location and parking',29],
 ];
export const calls = businesses.flatMap((business,b)=>definitions.map((d,i)=>({id:`${b?'MO':'JK'}-${1042-i}`,business:business.id,time:`${String(19-Math.floor(i/4)).padStart(2,'0')}:${String(42-i*4+(i>=4?16:0)).padStart(2,'0')}`,date:i===7?'2026-10-24':'2026-10-25',caller:`••• ${['0142','8821','3910','6604','2816','7730','9902','3421'][i]}`,intent:d[0],outcome:d[1],question:d[2],answer:d[3],summary:d[4],duration:d[5],booking:i===0||i===5,traceUrl:null})));
export function events(call){
 const list=[{title:'Call started',detail:'Restaurant greeting delivered',offset:'00:00'},{title:'Intent detected',detail:`${call.intent} · structured classification`,offset:'00:02'}];
 if(call.booking) return [...list,{title:'Availability checked',detail:call.summary+' · capacity available',offset:'00:05',source:'booking.yaml',payload:{tool:'check_table',party_size:call.id.endsWith('1042')?4:2,available:true}},{title:'Details read back',detail:'Date, time, party size, name and phone digits',offset:'00:32'},{title:'Caller confirmed',detail:'“Yes, that’s correct.”',offset:'00:38'},{title:'Booking created',detail:`Booking B-${call.id} · capacity rechecked`,offset:'00:40',source:'bookings.jsonl'}];
 if(call.outcome==='Transferred') return [...list,{title:'Transfer requested',detail:call.summary,offset:'00:08',payload:{tool:'transfer_to_human',reason:call.intent==='Order'?'food_order':'human_request'}},{title:'AI conversation ended',detail:'Demo handoff event; no real phone connection',offset:'00:10'}];
 if(call.outcome==='Needs review') return [...list,{title:'Knowledge searched',detail:'No result above relevance threshold',offset:'00:05',source:'docs/policies.md'},{title:'Fallback offered',detail:'Message or staff handoff offered; no answer invented',offset:'00:09'}];
 return [...list,{title:call.intent==='Menu'?'Dish checked':call.intent==='Hours'?'Hours retrieved':'Location retrieved',detail:call.summary,offset:'00:04',source:call.intent==='Menu'?'menu.yaml':'profile.yaml',payload:{result:call.answer}},{title:'Reply delivered',detail:'Response checked for brevity and grounding',offset:'00:08'}];
}
