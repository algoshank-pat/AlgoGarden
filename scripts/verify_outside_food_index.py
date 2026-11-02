"""Exercise real private index rebuilding and warm FAQ invalidation."""
import os,sys,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
out=ROOT/'results/cake-latency-20261028/index-check';out.mkdir(parents=True,exist_ok=False)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(out),LANGSMITH_TRACING='false')
import knowledge,policy_faq
target=out/'businesses/malnad_mane/docs';shutil.copytree(ROOT/'businesses/malnad_mane/docs',target)
knowledge.BUSINESSES=out/'businesses'
current=(target/'groups_events.md').read_text();old='Birthday cakes from outside are welcome if staff are informed in advance.'
p=target/'groups_events.md';p.write_text(current.replace('All outside food, including birthday cakes, homemade cakes and outside desserts, is prohibited; there is no exception for advance notice or staff permission.',old))
knowledge.ingest('malnad_mane');query='Is a birthday cake from outside okay?'
warm=knowledge.search('malnad_mane',query);assert old in warm['chunks'][0]['text']
p.write_text(current)
fresh=knowledge.search('malnad_mane',query)
assert fresh['source_digest']!=warm['source_digest'] and all(old not in c['text'] for c in fresh['chunks'])
faq=policy_faq.lookup
try:
 policy_faq.lookup=lambda *a,**k:None
 stale=knowledge.search('malnad_mane',query);assert not stale['relevant'] and not stale['chunks']
finally:policy_faq.lookup=faq
knowledge.ingest('malnad_mane')
collection=knowledge.chroma_client().get_collection(knowledge.collection_name('malnad_mane'),embedding_function=None)
docs=collection.get()['documents'];assert all(old not in text for text in docs)
assert collection.metadata['digest']==knowledge.digest(knowledge.source_documents('malnad_mane'))
try:
 policy_faq.lookup=lambda *a,**k:None
 rag=knowledge.search('malnad_mane','What is your policy for outside birthday cakes and food?')
 assert rag['relevant'] and any('is prohibited' in c['text'] for c in rag['chunks'])
finally:policy_faq.lookup=faq
result={'warm_permission_invalidated':True,'stale_vectors_fail_closed':True,'obsolete_chunks_removed':True,'rebuilt_rag_returns_prohibition':True,'policy_digest':collection.metadata['digest'],'private':True}
(out/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
