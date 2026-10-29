"""Stored pet FAQs stay grounded without inference or vector retrieval."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
import knowledge as rag
from policy_faq import lookup,_snapshot
from policy_requests import select_quotes

@pytest.fixture
def sources(monkeypatch,tmp_path):
    monkeypatch.setattr(rag,'BUSINESSES',tmp_path)
    monkeypatch.setattr(rag,'embed',Mock(side_effect=AssertionError('FAQ must not embed')))
    monkeypatch.setattr(rag,'chroma_client',Mock(side_effect=AssertionError('FAQ must not open Chroma')))
    _snapshot.cache_clear()
    for business,text in [('first','Pets are not permitted indoors.\nGuests with an assistance animal should contact staff before arriving.'),('second','Pets are welcome on request.')]:
        folder=tmp_path/business/'docs';folder.mkdir(parents=True)
        (folder/'pets.md').write_text(text,encoding='utf-8')
    return tmp_path

@pytest.mark.parametrize('question',['Can I bring my dog?','Do you allow dogs?','Are you pet-friendly?','Could I take my puppy with me?'])
def test_first_and_repeated_faq_need_no_models_or_index(sources,question):
    for _ in range(2):
        result=rag.search('first',question)
        assert result['relevant'] and result['retrieval_mode']=='source_faq_cache'
        quotes=select_quotes(question,[{'source':c['source'],'text':sentence} for c in result['chunks'] for sentence in c['sentences']])
        assert quotes[0]['text']=='Pets are not permitted indoors.'
    rag.embed.assert_not_called();rag.chroma_client.assert_not_called()

def test_assistance_dog_gets_its_condition_not_blanket_no(sources):
    result=rag.search('first','May I bring my guide dog?')
    quotes=select_quotes('May I bring my guide dog?',[{'source':c['source'],'text':s} for c in result['chunks'] for s in c['sentences']])
    assert len(quotes)==1 and 'contact staff before arriving' in quotes[0]['text']

def test_file_edit_invalidates_stored_answer_immediately(sources):
    old=rag.search('first','Can I bring my dog?')
    (sources/'first/docs/pets.md').write_text('Pets are welcome with staff approval.',encoding='utf-8')
    new=rag.search('first','Can I bring my dog?')
    assert new['source_digest']!=old['source_digest']
    assert new['chunks'][0]['sentences']==['Pets are welcome with staff approval.']

def test_deleted_source_never_serves_old_answer(sources):
    assert rag.search('first','Can I bring my dog?')['relevant']
    (sources/'first/docs/pets.md').unlink()
    assert not rag.search('first','Can I bring my dog?')['relevant']

def test_concurrent_restaurants_and_response_mutation_are_isolated(sources):
    names=['first','second']*10
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda b:rag.search(b,'Can I bring my dog?'),names))
    for business,result in zip(names,results):
        assert result['business']==business
        assert ('not permitted' in result['chunks'][0]['text'])==(business=='first')
    results[0]['chunks'][0]['sentences'].clear()
    assert rag.search('first','Can I bring my dog?')['chunks'][0]['sentences']

@pytest.mark.parametrize('question',['Can I bring my dog on the patio?','What are the pet fees?','Can I bring my dog and what is your corkage policy?','Can you book a table for two?','What are your opening hours?'])
def test_other_topics_or_unstored_pet_details_do_not_use_faq(question):
    assert lookup('first',question,{'pets.md':'Pets are not permitted indoors.'}) is None

@pytest.mark.parametrize('channel',['chat','voice'])
def test_public_turn_preserves_booking_and_has_zero_generations(client,monkeypatch,channel):
    import chat_api as api
    from information_requests import pending_state
    from tests.test_customer_feedback import say
    monkeypatch.setattr(rag,'embed',Mock(side_effect=AssertionError('No embeddings')))
    monkeypatch.setattr(rag,'chroma_client',Mock(side_effect=AssertionError('No vector DB')))
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
    b.reserving=True;b._awaiting_slot='phone';b.slots={'party_size':2,'date':'2027-01-12','time':'19:00'}
    before=pending_state(b);result=say(client,sid,'Can I bring my dog?')
    assert 'Pets are not permitted in the indoor dining area.' in result['reply']
    assert result['sources']==['kids_pets.md'] and pending_state(b)==before
    assert result['structured']['tools_called']==['search_knowledge']
    assert not any(c['component']=='ollama.chat' for c in result['structured']['calls'])
    b.chat.assert_not_called();rag.embed.assert_not_called();rag.chroma_client.assert_not_called()

# Reuse the private API fixture; never point these tests at production state.
from tests.test_customer_feedback import client
