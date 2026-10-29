"""Private regressions with opt-in pooled MCP; production settings are unchanged."""
import os
from pathlib import Path
ROOT=Path(__file__).parent
if __name__=='__main__':
 data=ROOT/'results/mcp-lookup/final-unit-data';data.mkdir(parents=True,exist_ok=True)
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(data),RECEPTIONIST_MCP_MODE='pooled',LANGSMITH_TRACING='false')
 import receptionist as app
 original=app.load_restaurant
 def fixture_restaurant(path):
  r=original(path)
  if Path(path).resolve()==(ROOT/'restaurants/demo_restaurant.yaml').resolve():r={**r,'phone_region':'IN','timezone':'Asia/Kolkata'}
  return r
 app.load_restaurant=fixture_restaurant
 # API configuration contracts follow the current live configuration; component
 # phone fixtures above stay fixed without editing that configuration file.
 import chat_api
 chat_api.restaurant=original(ROOT/'restaurants/demo_restaurant.yaml')
 import pytest
 files=['test_chat_api','test_routing','test_returning_customer','test_reservation_changes','test_reservation_alternatives','test_booking_db','test_booking_dates','test_booking_context','test_in_person_cutoff','test_reservation_schedule','test_decision_engine','test_decision_protocol','test_customer_feedback','test_input_decisions','test_live_no_correction','test_speech_text','test_mcp_pool','test_information_requests','test_policy_routing','test_dietary_intent_feedback','test_knowledge','test_structured_io']
 raise SystemExit(pytest.main(['tests/'+f+'.py' for f in files]+['-q','--basetemp',str(ROOT/'results/mcp-lookup/final-pytest')]))
