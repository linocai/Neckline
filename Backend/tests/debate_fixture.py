"""Explicit successful debate responses; morning/model failure fixtures remain raw."""
import json

def debate_text(text, summary=None):
    return json.dumps({"fullText":text,"summary": summary if summary is not None else {
        "commonFacts":["只有送样说法，没有量产订单。"],
        "disagreements":["送样是否足以证明短期受益。"],
        "unknowns":["客户身份与具体收入占比。"]}},ensure_ascii=False)
