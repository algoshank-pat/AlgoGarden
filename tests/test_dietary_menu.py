from unittest.mock import Mock
import pytest
import receptionist as app

@pytest.fixture(params=['chat','voice'])
def bot(request):
 r=app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml')
 b=app.Receptionist(r,chat=Mock(side_effect=AssertionError('No inference for recorded dietary facts')))
 b.tools.booking_source='phone' if request.param=='voice' else 'chat'
 return b

@pytest.mark.parametrize('question',['What vegan dishes do you have?','What gluten-free options are available?'])
def test_explicit_options_and_short_voice(bot,question):
 reply,tools=bot.turn(question)
 assert 'Tomato Basil Risotto' in reply and 'Chickpea & Spinach Polenta' in reply
 if bot.tools.booking_source=='phone':
  assert 'website' in reply and 'Peanut Chilli Spaghetti' not in reply
 assert 'check_dish' in tools

def test_specific_peanuts_and_gluten(bot):
 reply,_=bot.turn('What is Peanut Chilli Spaghetti? Is it vegan, gluten free and nut free?')
 assert 'Listed as vegan' in reply and 'Not gluten-free' in reply and 'Contains peanuts' in reply
 assert 'Wheat spaghetti' in reply

def test_unknown_nut_free_is_never_promised(bot):
 reply,_=bot.turn('Is Tomato Basil Risotto nut-free?')
 assert 'Nut-free status is not confirmed' in reply
 assert 'Listed as nut-free' not in reply

def test_unlabelled_vegan_status_not_inferred(bot):
 reply,_=bot.turn('Is Mushroom Pepper Fry vegan?')
 assert 'vegan status is not confirmed' in reply

def test_dairy_not_vegan(bot):
 reply,_=bot.turn('Is Burrata Chaat vegan?')
 assert 'Not vegan' in reply

def test_combined_restrictions(bot):
 reply,_=bot.turn('What is vegan and gluten-free?')
 assert 'Peanut Chilli Spaghetti' not in reply
 assert 'Tomato Basil Risotto' in reply

def test_safety_not_guaranteed(bot):
 reply,_=bot.turn('Do you guarantee gluten-free food is safe for celiac disease?')
 assert 'cannot guarantee' in reply

def test_chat_peanut_warning(bot):
 if bot.tools.booking_source=='phone':return
 reply,_=bot.turn('What vegan dishes do you have?')
 assert 'Peanut Chilli Spaghetti (contains peanuts)' in reply


def test_voice_new_choice_preserves_request_after_dietary_interruption(bot):
 from types import SimpleNamespace
 from chat_api import voice_reservation_turn
 original='Book a table for two on December 17, 2026 at 11 am'
 bot._dietary_followup=True
 bot.turn=Mock(return_value=('What phone number can I use for your reservation?',[]))
 session=SimpleNamespace(bot=bot,pending_reservation_request=original)
 reply,_=voice_reservation_turn(session,'No, I do not have a reservation.')
 assert session.pending_reservation_request is None
 assert bot._dietary_followup is False
 assert bot.turn.call_args.args[0]==original+'\nNo, I do not have a reservation.'
 assert 'phone' in reply


@pytest.mark.parametrize('phrase',['Yes, that is correct.',"Yes, that's correct.",'That is correct.'])
def test_natural_confirmation_uses_no_model(bot,phrase):
 from decision_engine import rule_fields
 bot.reserving=bot.awaiting_confirmation=True
 bot.slots={'name':'Demo','phone':'2025550194','date':'2026-12-17','time':'11:00','party_size':2}
 assert rule_fields(bot,phrase)=={}
 from information_requests import confirmation_control
 assert confirmation_control(phrase)
 bot.tools.book_table=Mock(return_value={'booked':True,'id':'test','date':'2026-12-17','time':'11:00'})
 reply,called=bot._reservation(phrase)
 assert bot.tools.book_table.call_count==1
 assert 'book_table' in called


def test_resumed_change_readback_clears_dietary_followup(bot):
 from information_requests import remember_readback,information_turn
 bot.reservation_changes.begin('cancel','2025550140')
 bot.reservation_changes.current={'id':'demo','name':'Demo','date':'2027-01-14','time':'19:00','party_size':2,'version':1,'phone':'2025550140'}
 bot.reservation_changes.pending=True
 bot._last_agent_reply='Cancel this reservation? Please say yes to confirm.'
 remember_readback(bot);bot._dietary_followup=True
 reply,_=information_turn(bot,'Yes, please.')
 assert 'confirm' in reply
 assert bot._dietary_followup is False
 assert bot.reservation_changes.pending
