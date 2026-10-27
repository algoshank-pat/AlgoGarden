"""Code-owned lookup, read-back and confirmation for existing reservations."""
import json
import re
from uuid import uuid4

from local_audit import event
from observability import traced_chat
from customer_identity import clock_context
from routing import repeat_or_handoff


def change_intent(text: str) -> str | None:
    match=re.search(r'\b(cancel|modify|change|update|reschedule|edit)\s+(?:(?:my|our|the|this|a|an|existing)\s+)*(?:reservation|booking|table|it)\b',text.lower())
    if match:return 'cancel' if match.group(1)=='cancel' else 'update'
    if re.search(r'\b(check|find|look up|view|see|lookup)\b.*\b(reservations?|bookings?|table)\b',text.lower()):return 'lookup'
    return None


class ReservationChanges:
    def __init__(self,bot):
        self.bot=bot
        self.action=None

    @property
    def active(self):
        return self.action is not None

    def begin(self,action: str,phone: str | None=None):
        self.action=action
        self.phone=phone
        self.reference=None
        self.current=None
        self.changes={}
        self.pending=False
        self.failures=0
        self.matches=[]
        self.selection_offered=False
        self.offered_alternatives=[]

    def turn(self,text: str):
        called=[]
        choice=text.strip().lower().rstrip('.!').replace(',','')
        if self.action=='update' and self.pending and choice in {'no','no thanks','no thank you'}:
            self.pending=False
            self.changes={}
            self.offered_alternatives=[]
            return 'No changes were made to your reservation. What other date, time or party size would you like?',[]
        if self.action=='update' and self.current is not None and not self.pending and not self.changes and choice in {'yes','yes please','correct','confirm'}:
            return 'What new date, time or party size would you like? I’ll read it back before making any changes.',[]
        if choice in {'no','no thanks','stop','never mind','nevermind','keep it','keep it unchanged','leave unchanged',"don't cancel",'do not cancel','keep original'} or (choice=='cancel' and self.action=='update'):
            self.action=None
            return 'No changes were made to your reservation.',[]
        requested=change_intent(text)
        if self.action=='lookup' and choice in {'cancel','modify','change','update'}:
            requested='cancel' if choice=='cancel' else 'update'
        if requested and requested!=self.action:
            self.action=requested
            self.pending=False
            self.changes={}
            self.offered_alternatives=[]
        if self.pending and choice in {'yes','yes please','correct',"that's correct",'confirm'}:
            event('reservation.change_confirmed',{'action':self.action})
            result=self.bot.tools.change_reservation(self.current['id'],self.phone,self.action,
                self.current['version'],self.changes,self.request_key)
            called.append('change_reservation')
            self.pending=False
            if result.get('changed'):
                action=self.action
                self.action=None
                if action=='cancel':
                    return 'Your reservation has been cancelled.',called
                row=result['reservation']
                return f"Your reservation is updated to {row['party_size']} guests on {row['date']} at {row['time']}.",called
            self.current=None
            self.changes={}
            if result.get('alternatives'):
                self.offered_alternatives=result['alternatives']
                from receptionist import alternative_question
                return f"No changes were made: {result.get('reason','that slot is unavailable.')} "+alternative_question(result['alternatives']),called
            return f"No changes were made: {result.get('reason','the request could not be completed')} Please confirm the phone number so I can look it up again.",called
        # Any correction invalidates the pending action until a new read-back.
        self.pending=False
        properties={key:{'type':['integer','null'] if key=='party_size' else ['string','null']}
                    for key in ('reference','phone','date','time','party_size')}
        properties['reservation_choice']={'type':['integer','null'],'description':'Number of the reservation option explicitly chosen by the caller; null unless chosen.'}
        try:
            from customer_identity import normalize_phone, phone_from_utterance
            literal_phone=phone_from_utterance(text,self.bot.restaurant.get('phone_region','IN'))
            option=re.search(r'\b(first|second|third|1st|2nd|3rd)\s+(?:option|one|suggestion)\b',text,re.I)
            if option and self.offered_alternatives:
                index={'first':0,'second':1,'third':2,'1st':0,'2nd':1,'3rd':2}[option[1].lower()]
                if index>=len(self.offered_alternatives):
                    return 'Please choose one of the available alternatives I offered.',[]
                extracted=dict(self.offered_alternatives[index])
            elif literal_phone:
                extracted={'phone':literal_phone}
            elif getattr(self.bot,'_routed_fields',None) is not None:
                extracted=self.bot._routed_fields
                self.bot._routed_fields=None
            else:
                response=traced_chat(self.bot.chat,model=self.bot.model,think=False,options={'temperature':0},
                    format={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False},messages=[
                        {'role':'system','content':clock_context(self.bot.restaurant)+'Extract reservation fields explicitly provided by the user as JSON. Missing fields are null. Keep date and time in the user words. Reference is an optional booking code, never a phone number; join spelled code characters. reservation_choice means an explicitly chosen numbered option (first=1, second=2), otherwise null. Never choose an option based on requested new details.'},
                        {'role':'user','content':text}])
                extracted=json.loads(response.message.content)
            if not isinstance(extracted,dict):
                raise ValueError('Invalid extraction')
            # Phone digits are already transcribed; preserve the literal field
            # rather than letting extraction omit or rewrite repeated digits.
            literal=re.search(r'(?:phone(?:\s+number)?|telephone|contact number|my number)\s*(?:is|:)?\s*((?:plus\s*)?\+?\d[\d\s().-]*\d)',text,re.I)
            if literal:extracted['phone']=re.sub(r'^plus\s*','+',literal[1],flags=re.I)
            elif literal_phone:extracted['phone']=literal_phone
            if isinstance(extracted.get('reference'),str) and isinstance(extracted.get('phone'),str):
                if re.sub(r'\D','',extracted['reference'])==re.sub(r'\D','',extracted['phone']) and not re.search(r'[a-f]',extracted['reference'],re.I):
                    extracted['reference']=None
            # Preserve explicit half-past wording if extraction drops minutes.
            half_past=re.search(r'\bhalf\s+past\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\b(?:\s+(?:(?:in the|at)\s+)?(?:morning|afternoon|evening|night|a\.?m\.?|p\.?m\.?))?',text,re.I)
            if half_past:extracted['time']=half_past[0]
        except Exception:
            return repeat_or_handoff(self.bot)
        if isinstance(extracted.get('reference'),str):
            reference=re.sub(r'[\s-]','',extracted['reference']).lower()
            if re.fullmatch(r'[a-f0-9]{8,32}',reference):
                if self.reference!=reference:
                    self.current=None
                self.reference=reference
            else:
                self.reference=self.current=None
                self.matches=[]
                self.selection_offered=False
                return 'That reference is invalid. Please give your phone number so I can find the reservation without a reference.',[]
        if isinstance(extracted.get('phone'),str):
            from customer_identity import normalize_phone
            phone=normalize_phone(extracted['phone'],self.bot.restaurant.get('phone_region','IN'))
            if self.phone!=phone:
                self.current=None
                self.matches=[]
                self.selection_offered=False
            self.phone=phone
        if self.action=='lookup' and self.current is not None and any(
                type(extracted.get(key)) is int if key=='party_size' else isinstance(extracted.get(key),str) and bool(extracted[key].strip())
                for key in ('party_size','date','time')):
            self.action='update'
        if self.action=='update':
            for key in ('date','time','party_size'):
                value=extracted.get(key)
                if (key=='party_size' and type(value) is int) or (key!='party_size' and isinstance(value,str) and value.strip()):
                    self.changes[key]=value
        if not self.phone:
            return 'What phone number was used for the reservation?',[]
        if self.current is None:
            try:
                if self.reference:
                    result=self.bot.tools.lookup_reservation(self.reference,self.phone)
                    called.append('lookup_reservation')
                    self.current=result.get('reservation') if result.get('found') else None
                else:
                    if not self.matches:
                        result=self.bot.tools.lookup_reservations_by_phone(self.phone)
                        called.append('lookup_reservations_by_phone')
                        self.matches=result['reservations']
                    if len(self.matches)==1:
                        self.current=self.matches[0]
                    elif len(self.matches)>1:
                        selection=extracted.get('reservation_choice')
                        if self.selection_offered and type(selection) is int and 1<=selection<=len(self.matches):
                            self.current=self.matches[selection-1]
                        else:
                            options='; '.join(f"{i}. {row['date']} at {row['time']} for {row['party_size']} guests" for i,row in enumerate(self.matches,1))
                            self.selection_offered=True
                            return f"I found multiple reservations: {options}. Which reservation do you mean? Please choose its number.",called
            except Exception:
                return 'I couldn’t access reservations right now. Please try again or ask staff; no changes were made.',called
            if not self.current:
                self.failures+=1
                if self.failures>=2:
                    self.bot.tools.transfer_to_human(reason='reservation_lookup_failed')
                    self.bot.ended=True
                    return 'I couldn’t match those details; staff can help find the reservation.',called+['transfer_to_human']
                return 'I couldn’t match an active upcoming reservation. Please check the phone number and try again.',called
            if self.current['status']!='active':
                self.action=None
                return 'That reservation is already cancelled; no changes were made.',called
        self.failures=0
        self.bot.failures=0
        if self.action=='lookup':
            row=self.current
            return f"Your reservation is for {row['party_size']} guests on {row['date']} at {row['time']}. Would you like to modify or cancel it?",called
        if self.action=='update':
            if not self.changes:
                return 'What date, time or party size would you like to change?',called
            prepared=self.bot.tools.prepare_reservation_change(self.current,self.changes)
            if not prepared.get('valid'):
                self.bot._availability_context={'requested':{k:({**self.current,**self.changes})[k] for k in ('date','time','party_size')},'reason':prepared.get('reason','Unavailable')}
                from receptionist import alternative_question
                self.changes={key:value for key,value in self.changes.items() if key=='party_size'}
                self.offered_alternatives=prepared.get('alternatives',[])
                return f"No changes were made: {prepared['reason']} "+alternative_question(prepared.get('alternatives',[])),called
            self.bot._availability_context=None
            self.changes=prepared['changes']
            self.offered_alternatives=[]
            if all(self.changes[key]==self.current[key] for key in ('date','time','party_size')):
                self.changes={}
                row=self.current
                return f"I found your reservation for {row['party_size']} guests on {row['date']} at {row['time']}. What would you like to change?",called
        self.pending=True
        self.request_key=str(uuid4())
        event('reservation.change_read_back',{'action':self.action,'version':self.current['version']})
        row=self.current
        phone=' '.join(c for c in self.phone if c.isdigit())
        if self.action=='cancel':
            return f"Cancel the reservation for {row['party_size']} guests on {row['date']} at {row['time']}, phone {phone}? Please say yes to confirm.",called
        after={**row,**self.changes}
        return f"Change {row['party_size']} guests on {row['date']} at {row['time']} to {after['party_size']} guests on {after['date']} at {after['time']}, phone {phone}. Is that correct?",called
