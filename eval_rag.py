"""Real local retrieval evaluation. Does not book or use an LLM judge."""
import json
from datetime import datetime,timezone
from pathlib import Path
import yaml
import knowledge


def main():
    root=Path(__file__).parent
    suite=yaml.safe_load((root/'tests/rag_cases.yaml').read_text())
    results=[]
    for business in suite['businesses']:
        for case in suite['cases']:
            found=knowledge.search(business,case['query'])
            sources=[chunk['source'] for chunk in found['chunks']]
            results.append({'business':business,'query':case['query'],'expected_source':case['source'],
                            'sources':sources,'passed':case['source'] in sources})
        for query in suite['negatives']:
            found=knowledge.search(business,query)
            results.append({'business':business,'query':query,'negative':True,
                            'passed':not found['relevant'],'sources':[c['source'] for c in found['chunks']]})
    positive=[row for row in results if not row.get('negative')]
    negative=[row for row in results if row.get('negative')]
    report={'at':datetime.now(timezone.utc).isoformat(),'embedding_model':knowledge.EMBED_MODEL,
            'retrieval_recall_at_4':sum(row['passed'] for row in positive)/len(positive),
            'negative_abstention':sum(row['passed'] for row in negative)/len(negative),
            'positive_cases':len(positive),'negative_cases':len(negative),'cases':results}
    destination=root/'results'
    destination.mkdir(exist_ok=True)
    path=destination/'rag-latest.json'
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='cases'},indent=2),flush=True)
    print('Report:',path)


if __name__=='__main__':
    main()
