import pytest
import chat_api as api
from tests.test_customer_feedback import client, say


@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('text',['Hello.','Hello!','Hi.','Hey!','Good morning.'])
def test_punctuated_greeting_never_routes_to_menu_or_model(client,channel,text):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 result=say(client,sid,text)
 assert 'Saffron & Basil' in result['reply'] and 'how can I help' in result['reply']
 assert not result['structured']['tools_called']
 api.sessions[sid].bot.chat.assert_not_called()
