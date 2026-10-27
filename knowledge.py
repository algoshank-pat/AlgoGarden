"""Restaurant-scoped local policy retrieval with explicit stale-index checks."""
import hashlib
import json
import os
import math
import os
import re
from pathlib import Path

import ollama
from local_audit import event
from observability import observed

HERE = Path(__file__).parent
BUSINESSES = HERE / 'businesses'
INDEX_PATH = Path(os.environ['RECEPTIONIST_FIXTURE_DIR'])/'chroma' if os.getenv('RECEPTIONIST_FIXTURE_DIR') else HERE/'data/chroma'
EMBED_MODEL = 'nomic-embed-text'
FALLBACK = 'I’m not sure about that policy; would you like to speak with a staff member?'


def business_docs(business: str) -> Path:
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', business):
        raise ValueError('Invalid restaurant identifier')
    return BUSINESSES / business / 'docs'


def source_documents(business: str) -> dict[str, str]:
    directory = business_docs(business)
    docs = {path.name: path.read_text(encoding='utf-8') for path in sorted(directory.glob('*.md'))}
    if not docs:
        raise ValueError('No policy Markdown documents found')
    return docs


def digest(docs: dict) -> str:
    return hashlib.sha256(json.dumps(docs, sort_keys=True).encode()).hexdigest()


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError('Invalid chunk size/overlap')
    tokens = list(re.finditer(r'\w+|[^\w\s]', text))
    chunks = []
    for start in range(0, len(tokens), size-overlap):
        end = min(start+size, len(tokens))
        chunks.append(text[tokens[start].start():tokens[end-1].end()])
        if end == len(tokens):
            break
    return chunks


def collection_name(business: str) -> str:
    business_docs(business)
    return 'policy_' + hashlib.sha256(business.encode()).hexdigest()[:24]


def chroma_client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(INDEX_PATH), settings=Settings(anonymized_telemetry=False))


@observed('knowledge.embed', 'embedding')
def embed(texts: list[str], query: bool = False) -> list:
    prefix = 'search_query: ' if query else 'search_document: '
    result = ollama.Client(timeout=90).embed(model=EMBED_MODEL, input=[prefix+text for text in texts], truncate=False)
    return result.embeddings


def ingest(business: str) -> dict:
    docs = source_documents(business)
    rows = []
    for source, text in docs.items():
        for index, chunk in enumerate(chunk_text(text)):
            key = hashlib.sha256(f'{source}:{index}:{chunk}'.encode()).hexdigest()
            rows.append((key, chunk, {'source':source,'business':business,'chunk':index}))
    if not rows:
        raise ValueError('Policy files are empty')
    # Prepare vectors before touching the live collection. Stop the API during ingestion.
    vectors = []
    for start in range(0,len(rows),16):
        vectors.extend(embed([row[1] for row in rows[start:start+16]]))
    collection = chroma_client().get_or_create_collection(collection_name(business),
        embedding_function=None, configuration={'hnsw':{'space':'cosine'}},
        metadata={'business':business,'state':'building'})
    collection.modify(metadata={'business':business,'state':'building'})
    collection.upsert(ids=[r[0] for r in rows], documents=[r[1] for r in rows],
                      metadatas=[r[2] for r in rows], embeddings=vectors)
    keep = {row[0] for row in rows}
    stale = [key for key in collection.get()['ids'] if key not in keep]
    if stale:
        collection.delete(ids=stale)
    collection.modify(metadata={'business':business,'state':'ready',
                                'digest':digest(docs),'embedding_model':EMBED_MODEL})
    return {'business':business,'documents':len(docs),'chunks':len(rows)}


def search(business: str, query: str, k: int = 4) -> dict:
    try:
        if not isinstance(query,str) or not query.strip() or len(query)>2000:
            raise ValueError('Invalid retrieval query')
        if type(k) is not int or not 1 <= k <= 4:
            raise ValueError('Retrieval k must be between 1 and 4')
        threshold = float(os.getenv('RAG_MIN_SIMILARITY','0.62'))
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('Invalid relevance threshold')
        docs = source_documents(business)
        collection = chroma_client().get_collection(collection_name(business), embedding_function=None)
        metadata = collection.metadata or {}
        if metadata.get('state') != 'ready' or metadata.get('digest') != digest(docs) or metadata.get('embedding_model') != EMBED_MODEL or metadata.get('business') != business:
            raise ValueError('Policy index is stale or incomplete; re-ingest')
        if not collection.count():
            raise ValueError('Empty index')
        result = collection.query(query_embeddings=embed([query],query=True),
                                  n_results=min(k,collection.count()),include=['documents','metadatas','distances'])
        chunks = []
        for key,text,meta,distance in zip(result['ids'][0],result['documents'][0],result['metadatas'][0],result['distances'][0]):
            score = 1-float(distance)
            if meta['business'] == business and score >= threshold:
                eligible = [sentence for sentence in sentences(docs[meta['source']]) if sentence in text and len(sentence)<=300 and len(sentence.split())<=60]
                if not eligible:
                    continue
                chunks.append({'id':key,'text':text,'source':meta['source'],'score':round(score,4),
                               'sentences':eligible})
        event('knowledge.retrieved',{'sources':sorted({c['source'] for c in chunks}),
                                    'scores':[c['score'] for c in chunks],'relevant':bool(chunks)})
        return {'relevant':bool(chunks),'chunks':chunks}
    except Exception:
        event('knowledge.unavailable')
        return {'relevant':False,'chunks':[],'reason':'No supported policy information is available.'}


def sentences(text: str) -> list[str]:
    lines = [re.sub(r'^\s*[-*]\s+','',line.strip()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    return [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+|\n+', '\n'.join(lines)) if sentence.strip()]


def grounded_answer(content: str, chunks: list[dict]) -> tuple[str,list[str]]:
    """Only complete, exact source sentences can be spoken as policy facts."""
    try:
        value = json.loads(content)
        if set(value) != {'quotes'} or not isinstance(value['quotes'],list) or not 1 <= len(value['quotes']) <= 2:
            raise ValueError('Expected one or two supported quotations')
        quotes, sources = [], []
        for choice in value['quotes']:
            if not isinstance(choice,dict) or set(choice) != {'source','text'}:
                raise ValueError('Invalid citation')
            valid = any(choice['source']==chunk['source'] and choice['text'] in chunk.get('sentences', sentences(chunk['text'])) for chunk in chunks)
            if not valid or len(choice['text'])>300:
                raise ValueError('Unsupported sentence')
            quotes.append(choice['text'])
            sources.append(choice['source'])
        answer = ' '.join(quotes)
        if len(answer.split())>60:
            raise ValueError('Answer too long for voice')
        return answer, sorted(set(sources))
    except (ValueError,TypeError,KeyError):
        return FALLBACK, []
