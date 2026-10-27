"""JSON contracts and safe serialization for local, inspectable handoffs."""
import dataclasses
import json
import math
import copy
from types import SimpleNamespace
from pathlib import Path
from datetime import date,datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict
from jsonschema import Draft202012Validator


class CallPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version: str='1.0'
    call_id: str
    component: str
    inputs: dict | None=None
    output: object=None
    error: dict | None=None


class LookupResult(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    found: bool
    reservations: list[dict]


class SpeechTranscript(BaseModel):
    model_config=ConfigDict(extra='allow',strict=True)
    text: str


class StructuredTurn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version: str='1.0'
    session_id: str
    request_id: str
    channel: Literal['chat','voice']
    input: dict
    output: dict
    routing: dict | None=None
    tools_called: list[str]
    calls: list[dict]


def json_value(value):
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else None
    if isinstance(value,Path):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,BaseModel):return json_value(value.model_dump(mode='json'))
    if dataclasses.is_dataclass(value):return json_value(dataclasses.asdict(value))
    if isinstance(value,dict):
        return {str(k):('[redacted]' if any(secret in str(k).lower() for secret in ('api_key','password','authorization','access_token')) else json_value(v)) for k,v in value.items() if not callable(v)}
    if isinstance(value,(list,tuple)):return [json_value(item) for item in value]
    if isinstance(value,SimpleNamespace):return json_value(vars(value))
    return {'type':type(value).__name__}


def validate_json(value,schema):
    errors=list(Draft202012Validator(schema).iter_errors(value))
    if errors:raise ValueError('Structured output violates JSON schema at '+'.'.join(map(str,errors[0].absolute_path)))
    return value


def validate_llm_response(response,options):
    response=copy.deepcopy(response)
    message=response.message
    tools={tool['function']['name']:tool['function']['parameters'] for tool in options.get('tools',[])}
    if getattr(message,'tool_calls',None):
        for call in message.tool_calls:
            if call.function.name not in tools:raise ValueError('Unknown structured tool call')
            validate_json(dict(call.function.arguments or {}),tools[call.function.name])
        return response
    schema=options.get('format')
    if not isinstance(schema,dict):raise ValueError('Every LLM call requires a JSON schema or structured tool calls')
    def invalid_constant(value):raise ValueError('Non-finite JSON number')
    value=json.loads(message.content,parse_constant=invalid_constant)
    if isinstance(value,dict):
        # Normalize omitted nullable slots to explicit null, never infer a value.
        for key in schema.get('required',[]):
            if key not in value and 'null' in schema.get('properties',{}).get(key,{}).get('type',[]):value[key]=None
    validate_json(value,schema)
    if isinstance(value,dict) and 'tool_calls' in value:
        for call in value['tool_calls']:
            if call['name'] not in tools:raise ValueError('Unknown structured tool call')
            validate_json(call['arguments'],tools[call['name']])
    message.content=json.dumps(value,ensure_ascii=False,allow_nan=False)
    return response
