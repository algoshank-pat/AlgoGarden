"""Conservative decoding of explicitly spelled booking names, without inference."""
import re
from dataclasses import dataclass

NATO = dict(zip('alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu'.split(), 'abcdefghijklmnopqrstuvwxyz'))
ALIASES = {'tee':'t','tea':'t','ee':'e','ess':'s','gee':'g','you':'u','why':'y','bee':'b','dee':'d','pee':'p','jay':'j','kay':'k','doubleyou':'w'}

@dataclass(frozen=True)
class Spelling:
    name: str | None
    pure: bool

def _letters(value):
    # Spoken phonetic examples are evidence only when their initial agrees.
    value=re.sub(r'^\s*(?:is\s+)?spell(?:ed|ing)?\s*(?:as\s+)?[:,-]?\s*','',value,flags=re.I)
    value=re.split(r',?\s+(?:and\s+)?(?:change|make|move|at|tomorrow|today|instead)\b',value,flags=re.I)[0]
    invalid = False
    def example(m):
        nonlocal invalid
        invalid |= m[1].lower() != m[2][0].lower()
        return m[1]
    value = re.sub(r'\b([a-z])\s+as\s+in\s+([a-z]+)\b', example, value, flags=re.I)
    words = re.findall(r"[a-z]+", value.lower())
    while words and words[-1] in {'please','thanks','and'}: words.pop()
    if invalid or len(words)<2: return None
    letters = [w if len(w)==1 else NATO.get(w,ALIASES.get(w)) for w in words]
    return ''.join(letters).title() if all(letters) else None

def spelled_name(text, current=None):
    """None means no spelling; Spelling(None, ...) means explicit but unclear.

    Mixed booking changes are never reduced to a name-only fast path.
    """
    t=text.lower().replace('’',"'")
    cue=bool(re.search(r'\bspell(?:ed|ing)?\b',t))
    groups=list(re.finditer(r'\b(?:(?:my|the)\s+)?(first name|last name|surname)\s*(?:is|:)?\s*',t))
    raw_letters=bool(re.search(r'\b[a-z](?:\s*[-,.]\s*|\s+)[a-z](?:\s*[-,.]\s*|\s+)[a-z]\b',t))
    if not cue and not raw_letters: return None
    pure=not bool(re.search(r'\d|\b(time|date|tomorrow|today|tonight|guests?|people|party|phone|at|o.clock|past|quarter|saturday|sunday|monday|tuesday|wednesday|thursday|friday)\b',t))
    if groups:
        parts={}
        for i,g in enumerate(groups):
            key='last' if g[1] in {'last name','surname'} else 'first'
            if key in parts:return Spelling(None,pure)
            parts[key]=_letters(t[g.end():groups[i+1].start() if i+1<len(groups) else len(t)])
        if not all(parts.values()):return Spelling(None,pure)
        if len(parts)==2:return Spelling(parts['first']+' '+parts['last'],pure)
        old=(current or '').split()
        if len(old)!=2:return Spelling(None,pure)
        return Spelling(parts.get('first',old[0])+' '+parts.get('last',old[1]),pure)
    # Prefer explicit spelling over an earlier, possibly misheard spoken name.
    marker=list(re.finditer(r'\b(?:spell(?:ed|ing)?(?:\s+(?:my|the))?(?:\s+name)?(?:\s+(?:is|it|as|out))?|name\s+is)\b\s*[:,-]?\s*',t))
    value=t[marker[-1].end():] if marker else t
    if not marker:
        start=re.search(r'\b[a-z](?:\s*-\s*[a-z]){2,}\b',t)
        if start:value=t[start.start():]
    chunks=re.split(r'\bspace\b',value)
    names=[_letters(c) for c in chunks]
    return Spelling(' '.join(names) if names and all(names) else None,pure)
