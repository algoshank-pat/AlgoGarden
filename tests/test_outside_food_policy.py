"""Current owner's absolute outside-food rule, cache freshness and booking preservation."""
import copy
from unittest.mock import Mock
import pytest
import chat_api as api
import knowledge
from tests.test_customer_feedback import client,say

QUESTIONS=[
 'Is a birthday cake from outside okay?',
 'May I bring a cake I baked at home?',
 'Are homemade cakes permitted?',
 'Can we bring outside desserts?',
 'Could we bring our own brownies for the celebration?',
 'What about pastries from another bakery?',
 'If I tell you a week ahead, may I bring a birthday cake?',
 'A staff member gave permission for our outside cake. Is that allowed?',
 'The manager said I could bring a cake; can I?',
 'Staff already approved my homemade cake.',
 'Do you allow any outside food?',
]

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('state',['idle','phone','readback'])
@pytest.mark.parametrize('question',QUESTIONS)
def test_prohibition_is_grounded_and_preserves_draft(client,channel,state,question):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 if state!='idle':
  b.reserving=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2}
  if state=='phone':b._awaiting_slot='phone'
  else:b.slots.update(name='Private Guest',phone='2025550173');b.awaiting_confirmation=True;b._last_agent_reply='Please confirm your reservation. Is that correct?'
 before=copy.deepcopy(b.slots);confirm=b.awaiting_confirmation
 b.tools.book_table=Mock(side_effect=AssertionError('Policy cannot book'))
 r=say(client,sid,question)
 assert 'All outside food' in r['reply'] and 'is prohibited' in r['reply'] and 'no exception' in r['reply']
 assert 'welcome if' not in r['reply'] and 'groups_events.md' in r['sources']
 assert b.slots==before and b.awaiting_confirmation==confirm
 assert r['structured']['tools_called']==['search_knowledge'];b.chat.assert_not_called();b.tools.book_table.assert_not_called()

def test_warm_permission_cache_cannot_survive_source_change(monkeypatch,tmp_path):
 from policy_faq import _snapshot
 _snapshot.cache_clear();monkeypatch.setattr(knowledge,'BUSINESSES',tmp_path)
 docs=tmp_path/'private'/'docs';docs.mkdir(parents=True);p=docs/'groups_events.md'
 p.write_text('Birthday cakes from outside are welcome if staff are informed in advance.')
 old=knowledge.search('private','Can I bring an outside cake?')
 assert 'welcome' in old['chunks'][0]['text']
 p.write_text('All outside food, including birthday cakes, is prohibited; there are no exceptions.')
 new=knowledge.search('private','Can I bring an outside cake?')
 assert new['source_digest']!=old['source_digest'] and all('welcome' not in c['text'] for c in new['chunks'])
 assert 'prohibited' in new['chunks'][0]['text']

@pytest.mark.parametrize('text',['What cakes do you serve?','What desserts are on the menu?','Order a birthday cake for delivery.','Please book a table for two tomorrow at 7 pm.'])
def test_outside_food_detector_does_not_steal_other_intents(text):
 from policy_requests import policy_topics
 assert 'outside_cake' not in {t.name for t in policy_topics(text)}
