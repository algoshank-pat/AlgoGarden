import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from local_audit import capture
from observability import traced_chat
from structured_io import validate_llm_response


def response(value):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(value),tool_calls=[]))


def test_nullable_slots_are_explicit_without_mutating_sdk_response():
    raw=response({'phone':'9900112233'})
    schema={'type':'object','properties':{'phone':{'type':['string','null']},'name':{'type':['string','null']}},'required':['phone','name'],'additionalProperties':False}
    value=validate_llm_response(raw,{'format':schema})
    assert json.loads(value.message.content)=={'phone':'9900112233','name':None}
    assert json.loads(raw.message.content)=={'phone':'9900112233'}


@pytest.mark.parametrize('value',[{'party_size':'four'},{'party_size':True},{'party_size':4,'invented':1}])
def test_bad_schema_fails_before_handoff(value):
    schema={'type':'object','properties':{'party_size':{'type':'integer'}},'required':['party_size'],'additionalProperties':False}
    with pytest.raises(ValueError):validate_llm_response(response(value),{'format':schema})


def test_actual_call_arguments_and_output_share_local_call_id():
    events=[]
    schema={'type':'object','properties':{'party_size':{'type':'integer'}},'required':['party_size']}
    with capture(events):
        traced_chat(Mock(return_value=response({'party_size':4})),model='test',format=schema,messages=[{'role':'user','content':'four people'}])
    start=next(e['details'] for e in events if e['name']=='ollama.chat.started')
    end=next(e['details'] for e in events if e['name']=='ollama.chat.completed')
    assert start['call_id']==end['call_id']
    assert start['inputs']['kwargs']['messages'][0]['content']=='four people'
    assert json.loads(end['output']['message']['content'])=={'party_size':4}


def test_structured_menu_action_then_structured_answer():
    import receptionist as app
    agent=app.MenuAgent(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'))
    agent.chat=Mock(side_effect=[response({'reply':'','tool_calls':[{'name':'get_menu','arguments':{}}]}),
                                response({'reply':'Here are the available dishes.','tool_calls':[]})])
    agent.tools.get_menu=Mock(return_value={'dishes':[{'name':'Test Dish','price_usd':100,'available':True}]})
    reply,called=agent.turn('What is on your menu?')
    assert reply=='We have Test Dish for $100.' and called==['get_menu']
    agent.chat.assert_called_once()
    assert all('format' in c.kwargs for c in agent.chat.call_args_list)
