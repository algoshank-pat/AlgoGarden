// Display message times in the restaurant's timezone.
export function conversationTimestamp(value,timeZone='America/Chicago'){
 if(!value)return '';
 const date=new Date(value);if(Number.isNaN(date.getTime()))return '';
 const parts=new Intl.DateTimeFormat('en-US',{timeZone,day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true}).formatToParts(date);
 const part=type=>parts.find(p=>p.type===type)?.value;
 const day=Number(part('day')),suffix=day%100>=11&&day%100<=13?'th':({1:'st',2:'nd',3:'rd'}[day%10]||'th');
 return `${day}${suffix} ${part('month')} ${part('year')} ${part('hour')}:${part('minute')} ${part('dayPeriod')}`;
}
