const requests={reservation:'Reservation request',hours:'Opening hours',location_parking:'Location & parking',menu:'Menu question',policy:'Restaurant policy',order:'Food order',human:'Staff assistance',other:'General enquiry'};
export function reservationLabel(row){
 if(!row)return '';
 if(row.party_size==null||!row.date||!row.time)return 'Reservation details are not available in this record.';
 const day=row.date?new Date(row.date+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}):'Date not recorded';
 const time=row.time?new Date('2000-01-01T'+row.time+':00').toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}):'Time not recorded';
 return `${row.party_size} guests · ${day} · ${time}`;
}
export function ownerAudit(call,history=[]){
 const events=call.events||call.turns.flatMap(t=>t.events||[]),milestones=[],known=new Map();let result=null,action=null,approvedAction=null,attention=false,handoff=false,contact=null;
 const add=(event,title,detail='',before=null,after=null)=>milestones.push({at:event.at,title,detail,before,after});
 for(const event of events){
  const details=event.details||{},output=details.output||details;
  if(event.name==='lookup_reservations_by_phone.completed'&&output.found){
   for(const row of output.reservations||[]){known.set(row.id,row);}
   contact=output.reservations?.length===1?output.reservations[0]:contact;
   add(event,(output.reservations||[]).length>1?'Multiple reservations found':'Existing reservation found',(output.reservations||[]).length>1?'The customer was asked which reservation they wanted to change.':reservationLabel(output.reservations?.[0]));
  }else if(event.name==='reservation.confirmed')add(event,'Customer confirmation received','The customer approved the booking.');
  else if(event.name==='reservation.read_back')add(event,'Booking details read back','The customer was asked to confirm before saving.');
  else if(event.name==='reservation.change_confirmed'){approvedAction=details.action;add(event,'Customer confirmation received',details.action==='cancel'?'The customer approved the cancellation.':'The customer approved the reservation change.');}
  else if(event.name==='book_table.completed'&&output.booked){
   const row=output.reservation||output;known.set(row.id,row);contact=row;result=row;action='Booked';add(event,'Reservation confirmed',reservationLabel(row),null,row);
  }else if(event.name==='change_reservation.completed'&&output.changed){
   const row=output.reservation;contact=row;result=row;action=(output.action||approvedAction)==='cancel'||row?.status==='cancelled'?'Cancelled':'Updated';
   const recorded=history.find(h=>h.booking_id===row?.id&&h.payload?.after?.version===row?.version);
   const before=recorded?.payload?.before||known.get(row?.id);if(row)known.set(row.id,row);
   add(event,action==='Cancelled'?'Reservation cancelled':'Reservation updated',action==='Cancelled'?'The reservation’s capacity was released.':'Confirmed changes were saved.',before,row);
  }else if(event.name==='transfer_to_human.completed'){attention=true;handoff=true;add(event,'Staff assistance requested','The customer was offered staff contact. This does not confirm a live transfer.');}
  else if(event.name.endsWith('.failed')){attention=true;add(event,'Request could not be completed','Check the customer’s request before following up.');}
  else if(event.name==='reservation.duplicate_blocked')add(event,'Duplicate booking prevented','An existing booking was retained.');
 }
 const meaningful=call.turns.find(t=>t.result?.routing?.intent&&!['other'].includes(t.result.routing.intent));
 const intent=meaningful?.result.routing.intent||call.intent;
 const needsFollowUp=attention&&call.review!=='Resolved';
 const outcome=action||(handoff?'Transferred':attention?'Needs review':intent==='reservation'?'Not completed':'Answered');
 return {request:requests[intent]||'Customer enquiry',outcome,result,contact,milestones,attention:needsFollowUp,
  heading:action==='Booked'?'A reservation was confirmed':action==='Updated'?'A reservation was updated':action==='Cancelled'?'A reservation was cancelled':attention?(needsFollowUp?'This customer may need your help':'Follow-up marked as resolved'):intent==='reservation'?'No reservation change was recorded':'The customer’s question was handled',
  explanation:action?(reservationLabel(result)||'The action was recorded, but the reservation details are unavailable in this older record.'):attention?(needsFollowUp?'Review the conversation and contact the customer if needed.':'The owner marked this conversation resolved. Follow-up notes are available below.'):intent==='reservation'?'Details may have been discussed, but no successful booking or change is recorded in this conversation.':'See the customer’s question and the receptionist’s response below.'};
}
