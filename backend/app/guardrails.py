"""Input and output guardrails.

Input: trade requests, advice/prediction requests and prompt injection are blocked
before any model sees the question. These are deterministic rules, so the refusal
rate on the eval set is reproducible; the LLM router is a second line of defence
(it can also return the `advice` and `trade` intents).

Output: advice wording is stripped from answers and every answer gets a data
timestamp and source line.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

log = logging.getLogger("tickersage.guardrails")

Category = Literal["trade", "advice", "injection", "off_topic"]

_F = re.I

# A question *about* a concept ("what is a limit order?") is not a request to act.
_CONCEPT_QUESTION = re.compile(
    r"^\s*(what|what's|whats|how|why|explain|define|describe|tell me about|is it true|in simple terms)\b", _F
)
_ACT_FOR_ME = re.compile(r"\b(for me|on my behalf|from my (account|wallet)|in my (account|wallet))\b", _F)

_TRADE_VERBS = r"(buy|sell|short|long|place|execute|submit|purchase|swap|convert|transfer|send|withdraw|stake|unstake|trade)"
_NOT_AN_ORDER = (
    r"(?!\s*(-|\s)?(term|answer|summary|version|story|order book|walls?|pressure|side|signals?|volume|offs?|ratio)\b)"
)

TRADE_PATTERNS = [
    # Imperative at the start of a sentence or chained with "and"/"then": "Buy 0.1 BTC", "...and place an order"
    re.compile(rf"(?:^|[.!?;:]\s*|\band\s+|\bthen\s+|\bjust\s+|\bplease\s+){_TRADE_VERBS}\b{_NOT_AN_ORDER}", _F),
    re.compile(rf"\b(can|could|would|will)\s+you\s+(please\s+)?(go\s+)?{_TRADE_VERBS}\b{_NOT_AN_ORDER}", _F),
    re.compile(r"\b(i\s+want|i'd\s+like|i\s+need)\s+you\s+to\s+" + _TRADE_VERBS, _F),
    re.compile(r"\b(market|limit|stop)[- ](buy|sell)\b", _F),
    re.compile(r"\b(execute|submit|place|put in|open|close)\s+(a|an|the|my)?\s*(\w+\s+){0,2}(order|trade|position)s?\b", _F),
    re.compile(r"\b(go|going)\s+(long|short)\s+(on\s+)?\w+", _F),
    re.compile(r"\b\d+\s?x\s+(long|short)\b", _F),
]
ACCOUNT_PATTERNS = [
    re.compile(r"\b(set|place|add|move|put|update|cancel)\s+(a\s+|my\s+|the\s+)?(stop[- ]?loss|take[- ]?profit|trailing\s+stop|"
               r"limit\s+order|order)\b", _F),
    re.compile(r"\bmy\s+(\w+\s+)?(positions?|holdings?|bags?|orders?|funds|coins)\b", _F),
    re.compile(r"\b(api[- ]?key|secret key|private key|seed phrase|mnemonic)\b", _F),
    re.compile(r"\b(connect|link|log ?in)\s+(to\s+)?my\s+(binance\s+|exchange\s+)?(account|wallet)\b", _F),
    re.compile(r"\bmy\s+(binance\s+|exchange\s+)?(account|wallet|balance|portfolio)\b", _F),
]

ADVICE_PATTERNS = [
    re.compile(r"\bshould\s+(i|we)\s+(still\s+)?(buy|sell|hold|invest|short|long|get\s+(in|out)|ape|dca|exit|enter|add|stake|keep|take\s+profits?)\b", _F),
    re.compile(
        r"\b(is|are)\s+(it|now|this|that|today|\w+)\s+(a\s+)?(good|bad|great|smart|safe|right|wise)\s+"
        r"(time\s+to\s+(buy|sell|invest|enter|exit|get\s+in)|idea\s+to\s+(buy|sell|invest)|investment|entry|bet|buy|sell)\b",
        _F,
    ),
    re.compile(r"\b(worth|good)\s+(buying|selling|investing|holding|getting)\b", _F),
    re.compile(r"\bis\s+(it|\w+)\s+a\s+(buy|sell|hold)\b", _F),
    re.compile(r"\b(price\s+)?(prediction|forecast)s?\b", _F),
    re.compile(r"\b(predict|forecast)\w*\b", _F),
    re.compile(r"\b(price\s+target|target\s+price)s?\b", _F),
    re.compile(r"\bwill\s+(\w+\s+){0,3}(go\s+up|go\s+down|rise|fall|drop|crash|pump|dump|moon|recover|rally|bounce|break\s+out|reach|hit|surpass|exceed|go\s+to|be\s+worth|flip|outperform)\b", _F),
    re.compile(r"\b(how\s+much|how\s+high|how\s+low|where)\s+(will|would|could)\s+\w+\s+(\w+\s+){0,2}(be|go|get|trade|reach)\b", _F),
    re.compile(r"\b(guarantee[ds]?|risk[- ]free|sure[- ]?fire|can'?t\s+lose|easy\s+money|\d+x\s+(gains?|returns?|profits?))\b", _F),
    re.compile(r"\b(best|top)\s+(\w+\s+){0,2}(coins?|cryptos?|tokens?|altcoins?|investments?|assets?)\s+(to\s+)?(buy|invest|hold|pick|ape)\b", _F),
    re.compile(r"\bwhat\s+(coins?|cryptos?|tokens?)\s+(should|to)\b", _F),
    re.compile(r"\b(best|top|good|right)\s+(\w+\s+){0,2}to\s+(buy|invest|hold|sell|ape)\b", _F),
    re.compile(r"\bwhich\s+(\w+\s+){0,2}(coins?|cryptos?|tokens?|altcoins?|one)\b.{0,40}\b(buy|invest|hold|pick)\b", _F),
    re.compile(r"\b(tell|show)\s+me\s+what\s+to\s+(buy|sell|invest\s+in)\b", _F),
    re.compile(r"\bwhat\s+(should\s+i|to)\s+(buy|sell|invest\s+in)\b", _F),
    # Broader shapes: any "should I/we ..." about acting on a position, opinion-seeking about direction,
    # "next 100x coin", valuation judgements.
    re.compile(r"\bshould\s+(i|we)\b(\s+\w+){0,4}\s+(buy|sell|hold|invest|load|get|add|exit|enter|ape|short|long|"
               r"dump|keep|accumulate|bet|stake|move|switch|rotate|wait)\w*\b", _F),
    re.compile(r"\b(do|would)\s+you\s+(think|reckon|believe|say)\b.{0,60}\b(should|buy|sell|hold|go\s+up|go\s+down|"
               r"rise|fall|pump|dump|worth|bottom|top|recover|moon)\w*\b", _F),
    re.compile(r"\bwhere\s+(do|would)\s+you\s+(see|expect|think)\b", _F),
    re.compile(r"\b(next|upcoming)\s+(\w+\s+){0,2}(coin|crypto|token|gem|\d+\s?x)\b", _F),
    re.compile(r"\b\d+\s?x\b.{0,30}\b(coin|crypto|token|gem|gains?|returns?)\b|\b(coin|crypto|token|gem)s?\b.{0,30}\b\d+\s?x\b", _F),
    re.compile(r"\b(undervalued|overvalued|cheap|expensive|bottomed|topped)\b.{0,40}\?|\b(has|is)\s+\w+\s+(bottomed|topped)\b", _F),
    re.compile(r"\bload(ing)?\s+up\s+on\b|\bstack(ing)?\s+(more\s+)?sats\b", _F),
    re.compile(r"\b(bullish|bearish)\s+or\s+(bullish|bearish)\b|\b(bullish|bearish)\b.{0,25}\b(next|tomorrow|this\s+week|"
               r"this\s+month|this\s+year)\b", _F),
    re.compile(r"\b(realistic|reasonable|your)\s+(price\s+)?(target|level|prediction|outlook)\b|\btargets?\s+for\s+\w+", _F),
    re.compile(r"\b(trade|trading|investment)\s+(recommendation|idea|signal|call|tip|setup)s?\b", _F),
    re.compile(r"\b(give|need|want|get)\s+(me\s+)?(some\s+)?(financial|investment|trading)\s+(advice|tips?|recommendations?)\b", _F),
    re.compile(r"\b(when|where)\s+(should|do|to)\s+(i\s+)?(buy|sell|enter|exit|take\s+profits?|set\s+(a\s+)?stop)\b", _F),
    re.compile(r"\b(buy|sell|hold)\s+(signal|recommendation|rating|call)s?\b", _F),
    re.compile(r"\b(all[- ]in|put\s+(all\s+)?my\s+(savings|money|life\s+savings|salary))\b", _F),
    re.compile(r"\b(would|will)\s+you\s+(buy|sell|invest|hold)\b", _F),
    re.compile(r"\bif\s+you\s+were\s+me\b", _F),
    re.compile(r"\b(entry|exit)\s+points?\b", _F),
    re.compile(r"\bgoing\s+to\s+(go\s+up|go\s+down|rise|fall|crash|moon|pump|dump|recover)\b", _F),
]

INJECTION_PATTERNS = [
    re.compile(r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b.{0,40}\b(instructions?|rules?|guidelines?|polic(y|ies)|prompts?|restrictions?|guardrails?|safety|constraints?|programming)\b", _F),
    re.compile(r"\b(ignore|disregard|forget)\s+(all\s+|everything\s+|the\s+|what\s+(is|was)\s+)?(previous|above|prior|earlier)\b", _F),
    re.compile(r"\b(debug|maintenance|test|god|developer|admin|root|unrestricted)\s+mode\b", _F),
    re.compile(r"\b(disable|turn\s+off|remove|bypass|deactivate)\s+(your\s+|the\s+|all\s+)?(safety|filters?|guardrails?|"
               r"restrictions?|rules|content\s+polic(y|ies))", _F),
    re.compile(r"\bi('m|\s+am)\s+(the|your)\s+(developer|admin|administrator|creator|owner|operator)\b", _F),
    re.compile(r"\b(reveal|show|print|repeat|output|leak|dump|display)\b.{0,40}\b(system\s+prompt|instructions|developer\s+message|hidden\s+prompt|your\s+prompt|your\s+rules)\b", _F),
    re.compile(r"\b(system\s+prompt|developer\s+message|hidden\s+instructions?)\b", _F),
    re.compile(r"\b(output|print|repeat|show|reveal|display)\b.{0,30}\b(everything|all|text|content|words?)\s+(above|before|preceding|prior)\b", _F),
    re.compile(r"\b(above|previous|prior)\s+(this\s+)?(line|message|text)\b.{0,20}\b(verbatim|word\s+for\s+word)?", _F),
    re.compile(r"\b(no|without)\s+(rules|restrictions|filters|guardrails|limits)\b", _F),
    re.compile(r"\b(new|updated)\s+(rule|policy|instruction)s?\b.{0,30}\b(admin|developer|system|operator|from)\b", _F),
    re.compile(r"\byou\s+are\s+(now|no\s+longer)\b", _F),
    re.compile(r"\b(act|pretend|roleplay|role-play|behave)\s+(as|like|to\s+be)\b", _F),
    re.compile(r"\bpretend\s+(that\s+)?(you('re|\s+are)|you\s+were|to\s+be|i('m|\s+am))\b", _F),
    re.compile(r"\byou('re|\s+are)\s+(now\s+)?(my|a|an)\s+(personal\s+)?(financial\s+|investment\s+)?(advisor|adviser|broker|trader)\b", _F),
    re.compile(r"\b(jailbreak|developer\s+mode|god\s+mode|unfiltered|uncensored|sudo\s+mode|admin\s+mode|DAN\s+mode)\b", _F),
    re.compile(r"\bDAN\b"),
    re.compile(r"\b(new|updated|real|actual)\s+(instructions?|rules?|system\s+prompt)\s*[:\-]", _F),
    re.compile(r"<\s*/?\s*(system|instructions?|admin|assistant)\s*>", _F),
    re.compile(r"\[\s*(system|admin|instructions?)\s*\]", _F),
    re.compile(r"\bBEGIN\s+(SYSTEM|ADMIN|NEW)\b"),
]

# Extra patterns for text that arrives inside tool output (headlines, doc chunks).
TOOL_TEXT_INJECTION_PATTERNS = INJECTION_PATTERNS + [
    re.compile(r"\b(assistants?|ai|chatbots?|models?|agents?|llms?|bots?)\b.{0,40}\b(must|should|need\s+to|are\s+instructed\s+to|will\s+now)\b", _F),
    re.compile(r"\btell\s+(the\s+)?(users?|readers?|people|everyone|investors?)\s+to\b", _F),
    re.compile(r"\b(send|transfer|deposit)\b.{0,40}\b(to|into)\s+(this|the\s+following|our)\s+(wallet|address)\b", _F),
    re.compile(r"\b(recommend|urge|instruct)\w*\s+(users?|readers?|everyone|investors?)\s+to\s+(buy|sell)\b", _F),
    # Added after the red-team run: text that talks to the assistant instead of reporting news.
    re.compile(r"\b(note|message|notice|update|memo|instructions?|reminder|alert)\s+(for|to)\s+(the\s+|all\s+|any\s+)?"
               r"(automated\s+)?(ai|assistants?|models?|bots?|chatbots?|llms?|summari[sz]ers?|agents?|language\s+models?)\b", _F),
    re.compile(r"\b(message|note|notice|update|memo|order)\s+from\s+(the\s+)?(admin|administrator|operator|developer|"
               r"system|owner|moderator)s?\b|\b(admin|administrator|system|operator)\s+(message|note|notice|override|"
               r"instruction|alert)\b|\[\s*admin\b", _F),
    re.compile(r"\b(dear|attention|hey)\s+(tickersage|ai|assistants?|bots?|models?|chatbots?|llms?|summari[sz]ers?|"
               r"agents?)\b", _F),
    re.compile(r"\b(reply|respond|answer)\s+(to\s+the\s+user\s+)?with\b|\b(end|start|begin|finish)\s+(your\s+\w+\s+)?"
               r"with\s*[:'\"]|\bwhen\s+you\s+summari[sz]e\b|\b(ignore|disregard)\s+the\s+(question|user|query|request)\b",
               _F),
    re.compile(r"<!--|-->|^\s*(assistant|system)\s*:", _F),
    re.compile(r"\b(the\s+)?users?\s+(has|have|is)\s+(authori[sz]ed|approved|consented|a\s+verified|verified)\b", _F),
    re.compile(r"\bforget\s+(everything|all\s+(you|of)|what\s+you)\b", _F),
    re.compile(r"\b(recommend|promote|push|shill|tell)\w*\b.{0,30}\bto\s+(every|all|each)\s+(readers?|users?|investors?)\b"
               r"|\btell\s+(the\s+|your\s+|all\s+|every\s+)?(users?|readers?|audience|investors?)\b", _F),
    re.compile(r"\b(should|must|need\s+to|immediately)\s+(move|send|transfer|deposit)\s+(their\s+|your\s+|all\s+)?"
               r"(funds|coins|tokens|assets|crypto|holdings|balances?)\b|\b(move|send|transfer|deposit)\b.{0,50}"
               r"\b0x[a-f0-9]{40}\b", _F),
    re.compile(r"\b(assistants?|ai|chatbots?|models?|agents?|llms?|bots?)\b.{0,40}\b(cleared|allowed|permitted|"
               r"authori[sz]ed)\s+to\b", _F),
    re.compile(r"\b(give|tell)\s+(them|the\s+user|users)\b.{0,30}\b(price\s+target|buy|sell)\b", _F),
    re.compile(r"\bmensaje\s+del?\s+(administrador|admin|sistema)\b|\b(a|para)\s+todos\s+los\s+usuarios\b", _F),
]

# ------------------------------------------------------------------ red-team hardening
# Added after the automated red-team run (evals/redteam_report.md). Three ideas:
# 1. every clause is checked on its own, so a benign opener ("What is a funding rate? Also, buy...")
#    no longer switches off the trade check for the rest of the message;
# 2. obfuscated text is decoded (zero-width characters, homoglyphs, leetspeak, split letters, base64,
#    rot13, reversed text, swapped-letter typos) and checked again;
# 3. role-play framing, prompt extraction and five more languages get their own patterns.

ROLEPLAY_PATTERNS = [
    re.compile(r"\b(stay|remain|keep|staying)\s+in\s+(character|role|persona)\b|\bin\s+character\b|"
               r"\bbreak(ing)?\s+character\b", _F),
    re.compile(r"\b(imagine|suppose|pretend|assume)\s+(that\s+)?you('re|\s+are|\s+were)\b", _F),
    re.compile(r"\bif\s+you\s+were\s+(a|an|the|my|some)\b", _F),
    re.compile(r"\bfrom\s+now\s+on,?\s+(you('ll|\s+will)?\s+)?(answer|respond|reply|act|speak|talk)\s+(only\s+)?(as|like)\b"
               r"|\bfrom\s+now\s+on,?\s+you('re|\s+are)\b", _F),
    re.compile(r"\byou('re|\s+are)\s+(?!not\b)(now\s+)?([\w,'-]+\s+){0,5}?(advisor|adviser|broker|trader|uncle|aunt|"
               r"grandma|grandmother|grandpa|mentor|banker|planner|guru|influencer|manager|whale|analyst|degen)s?\b", _F),
    re.compile(r"\byou('re|\s+are)\s+(now\s+)?(called|named)\b|\b(ai|bot|model|assistant|chatbot)\s+(that|which|who)\s+"
               r"(answers|can\s+do|will\s+do|ignores|never\s+refuses)\b|\banswers?\s+(everything|anything|any\s+question)\b",
               _F),
    re.compile(r"\b(no|without|zero)\s+(compliance|censorship|boundaries|ethics|morals|restraints|filter)\b", _F),
    re.compile(r"\b(simulate|write|create|generate|draft|compose|script)\s+(me\s+)?(a|an|the)?\s*(short\s+|fictional\s+|"
               r"funny\s+)?(scene|story|dialogue|conversation|script|screenplay|roleplay|chat|play|poem)\b.{0,100}"
               r"\b(buy|sell|which\s+coin|price\s+target|invest|accumulate|offload|dump|hold|load\s+up|ape|moon|\d+x|"
               r"where\s+[\w$]+\s+will\s+be|will\s+[\w$]+\s+(go|rise|fall))\b", _F),
    re.compile(r"\b(for\s+a|in\s+my|for\s+my)\s+(novel|story|book|screenplay|script|movie|game|fiction)\b.{0,120}"
               r"\b(buy|sell|which\s+coin|invest|price\s+target|what\s+(does|would)\s+(he|she|they)\s+say)\b", _F),
    re.compile(r"\b(be|play|act\s+as|act\s+like|become)\s+my\s+(late\s+|deceased\s+|dead\s+)?(grandma|grandmother|granny|"
               r"grandpa|grandfather|nana|mom|mother|dad|father|uncle|aunt|broker|advisor|adviser)\b|\bmy\s+(late\s+|"
               r"deceased\s+|dead\s+)?(grandma|grandmother|granny|grandpa|grandfather|nana)\s+(used\s+to|would|always)\b", _F),
    re.compile(r"\b(allowed|permitted|able|free)\s+to\s+give\s+(financial\s+|investment\s+|trading\s+)?(advice|"
               r"recommendations|signals|tips)\b", _F),
    re.compile(r"\b(answer|respond|reply)\s+(twice|two\s+ways|in\s+two\s+ways|both\s+as)\b|\bonce\s+as\b", _F),
    re.compile(r"\b(pay\s+no\s+attention\s+to|set\s+aside|never\s+mind|throw\s+out|discard|scrap)\b.{0,40}"
               r"\b(instructions?|rules?|guidelines?|prompts?|setup|directives?|programming|restrictions?)\b", _F),
    # A persona name right after "you are": "You are AlphaAI", "You're Satoshi Nakamoto" (case-sensitive).
    re.compile(r"\b[Yy]ou('re|\s+are)\s+(now\s+)?([A-Z][a-z]*[A-Z0-9]\w*|[A-Z][a-z]+\s+[A-Z][a-z]+)\b"),
]

_META_NOUNS = r"(instructions?|guidelines|directives?|prompts?|configuration|config|setup|initiali[sz]ation|programming|" \
              r"rules|polic(y|ies)|limits|constraints|restrictions)"
EXTRACTION_PATTERNS = [
    re.compile(r"\b(translate|summari[sz]e|paraphrase|list|spell(\s+out)?|quote|recite|repeat|print|output|show|share|"
               r"paste|reveal|tell\s+me|give\s+me|write|describe|encode|put|dump|copy|read\s+(out|back)|leak|disclose|"
               r"state|explain|what\s+(are|is|were|does))\b.{0,40}\byour\s+(\w+\s+){0,2}" + _META_NOUNS + r"\b", _F),
    re.compile(r"\b(system|developer|hidden|secret|router|planner|initiali[sz]ation|init|setup)\s+(message|prompt|"
               r"instructions?|text)\b|\b(initial|original|internal|base)\s+(prompt|instructions)\b", _F),
    re.compile(r"\b(rules|instructions?|guidelines|polic(y|ies)|limits|directives?|prompts?|constraints)\s+(that\s+)?"
               r"(you\s+were|were\s+you|you've\s+been|you\s+have\s+been|you\s+got)\s+(given|told|set|programmed|"
               r"instructed|configured|provided|trained|fed)\b|\bwhat\s+(were|have)\s+you\s+(been\s+)?(told|given|"
               r"instructed|programmed)\b|\bbefore\s+(this|our|the)\s+(conversation|chat|session)\s+(started|began)\b", _F),
    re.compile(r"\bprompt\s+(that\s+)?you\s+(run|were|are|use|operate)\b|\b(first|last)\s+\d+\s+(words|lines|tokens|"
               r"characters|sentences)\s+of\s+(your|the)\b|\byour\s+(context(\s+window)?|initiali[sz]ation)\b|"
               r"\bexact\s+wording\s+of\s+your\b", _F),
]

EVASION_PATTERNS = [
    re.compile(r"\b(so|to)\s+(that\s+)?(it|this|that|you)\s+(isn'?t|is\s+not|won'?t\s+be|doesn'?t\s+get|don'?t\s+get|"
               r"aren'?t)\s+(filtered|flagged|blocked|caught|detected|censored)\b", _F),
    re.compile(r"\b(bypass|avoid|evade|get\s+(past|around)|slip\s+past|dodge|trick)\s+(the\s+|your\s+|any\s+)?(filters?|"
               r"guardrails?|moderation|safety|detection|censors?)\b", _F),
    re.compile(r"\b(decode|decipher|reverse|unscramble|decrypt)\b.{0,60}\b(do\s+what\s+it\s+says|follow\s+it|obey|"
               r"execute\s+it|comply|carry\s+(it\s+)?out)\b", _F),
]

EXTRA_INJECTION_PATTERNS = ROLEPLAY_PATTERNS + EXTRACTION_PATTERNS + EVASION_PATTERNS

_ACTION = (r"(buy|sell|dca|invest|get(\s+(in|into|out))?|hold|exit|enter|ape|accumulate|pick\s+up|grab|load|short|long|"
           r"stake|take\s+profits?|dump|own|add|cash\s+out|offload)")
EXTRA_ADVICE_PATTERNS = [
    re.compile(r"\b(is|would|will)\s+it\s+(be\s+)?(a\s+)?(smart|smarter|wise|wiser|dumb|dumber|stupid|good|bad|better|"
               r"worse|too\s+late|too\s+early|right|the\s+right\s+time|good\s+time|mistake|good\s+idea|bad\s+idea|safe|"
               r"risky)\b.{0,30}\b" + _ACTION + r"\w*", _F),
    re.compile(r"\bmakes?\s+sense\s+(for\s+(me|us)\s+)?to\s+" + _ACTION, _F),
    re.compile(r"\b\d+\s?x\s+(from\s+here|again|this\s+(year|cycle|month|week))\b|\bcan\s+[\w$]+\s+(still\s+)?\d+\s?x\b", _F),
    re.compile(r"\bwhere('s|\s+is|\s+are|\s+will)\s+[\w$]+(\s+\w+)?\s+(headed|heading|going)\b", _F),
    re.compile(r"\b(what\s+(percentage|percent|portion|share|proportion|allocation)|how\s+much)\b.{0,40}"
               r"\b(should|would)\s+(i|we|you)\s+(own|have|allocate|hold|keep|put|invest|buy)\b", _F),
    re.compile(r"\bshould\s+(i|we)\s+(\w+\s+){0,3}(own|allocate|pick|grab|scoop|offload|unload|trim|average|put|cash|"
               r"go\s+(long|short))\w*\b", _F),
    re.compile(r"\b(hold|buy|sell|ape|stay)\s+or\s+(fold|sell|buy|hold|wait|dump|exit|run)\b(?!\s+(orders?|side|"
               r"pressure|walls?|signals?|volume|ratio))", _F),
    re.compile(r"\b(rate|rank|score|grade)\s+(\w+\s+){1,3}as\s+an?\s+(investment|buy|bet|hold|pick)\b", _F),
    re.compile(r"\b(good|best|ideal|nice|decent|great)\s+(entry|exit|entries|exits)\b", _F),
    re.compile(r"\b(most|more|biggest|best|highest|greatest)\s+(upside|potential|room\s+to\s+(run|grow))\b", _F),
    re.compile(r"\b(do|would|can|could)\s+you\s+(recommend|suggest|endorse)\b(?!\s+(a|an|some|any)\s+(good\s+)?(book|"
               r"resource|course|site|website|article|guide|tutorial|podcast|video|channel|paper|source|reading))", _F),
    re.compile(r"\benough\s+to\s+" + _ACTION, _F),
    re.compile(r"\b(what\s+(percentage|percent|portion|share|proportion|allocation)|how\s+much)\s+of\s+my\s+(portfolio|"
               r"savings|money|net\s+worth|stack)\b", _F),
    re.compile(r"\bwhere\s+[\w$]+\s+will\s+(be|go|trade|end\s+up|land)\b", _F),
    re.compile(r"\b(better|best|smarter)\s+(buy|investment|bet|pick)\b(?!-)", _F),
    re.compile(r"\bis\s+[\w$]+\s+a\s+(pick\s*up|steal|bargain|good\s+pick)\b|\b(what'?s|what\s+is)\s+the\s+(move|play)\s+"
               r"(on|with|for)\b", _F),
    re.compile(r"\b(about|gonna|set|poised|due|likely|expected)\s+(to\s+|for\s+)?(go\s+up|go\s+down|pump|dump|moon|crash|"
               r"explode|rally|break\s*out|tank|rip|surge|skyrocket)\b", _F),
    re.compile(r"\bgut\s+(feeling|call|check|instinct|says?)\b|\b(your|ur)\s+(honest\s+|personal\s+|real\s+)?(take|view|"
               r"opinion|thoughts?|outlook|feeling|read|call|sense)\s+(on|about|for)\s+[\w$]+.{0,40}\b(next|this|"
               r"tomorrow|coming|short[- ]term|long[- ]term|20\d\d|headed|future)\b", _F),
    re.compile(r"\b(thinking|planning|considering|tempted)\s+(about|of|to)\s+\w+.{0,40}\b(thoughts|what\s+do\s+you\s+"
               r"think|good\s+idea|worth\s+it|yay\s+or\s+nay|opinions?)\b", _F),
    re.compile(r"\bif\s+you\s+had\s+\$?\d|\bhow\s+would\s+you\s+(split|allocate|invest|divide|spend|deploy|put|play|trade)"
               r"\b|\bhow\s+much\b.{0,40}\b(would|should)\s+(you|i|we)\s+(put|allocate|invest|hold|own|buy|have)\b", _F),
    re.compile(r"\b(is|are)\s+[\w$]+(\s+\w+)?\s+going\s+(up|down|higher|lower)\b.{0,30}\b(tomorrow|overnight|next|soon|"
               r"this\s+(week|month|year)|later|again|from\s+here)\b", _F),
    re.compile(r"\bwill\s+(\w+\s+){0,3}(climb|head\s+(higher|lower|up|down)|surge|skyrocket|explode|tank|soar|plunge|"
               r"double|triple|go\s+parabolic|rebound)\b", _F),
    re.compile(r"\b(which|what)\s+(\w+\s+){0,2}(coins?|cryptos?|tokens?|altcoins?|one)\s+(to|should\s+i|do\s+i|would\s+"
               r"you)\s+(buy|sell|hold|pick|accumulate|grab|get|load|ape|invest|dump|stack|scoop|snag|own|short)\b"
               r"|\b(tell|show)\s+me\s+(which|what)\s+(coin|crypto|token|altcoin)s?\b", _F),
    re.compile(r"\b(buy|sell|hold|accumulate|exit|entry|long|short)\s+(or\s+\w+\s+)?(call|signal|recommendation|"
               r"rating)s?\b", _F),
]

EXTRA_TRADE_PATTERNS = [
    re.compile(r"\bon\s+my\s+behalf\b|\bmy\s+(\w+\s+)?(api|brokerage)\b", _F),
    re.compile(r"\b(close|open|cover|exit)\s+(my|the|a|an)\s+(\w+\s+){0,2}(short|long|position|trade)s?\b", _F),
    re.compile(r"\b(place|put\s+in|submit|enter|set)\s+(a|an|my)\s+(\w+\s+){0,2}(bid|offer)\b", _F),
    re.compile(r"\b(authori[sz]e|allow|permit)\s+you\s+to\s+(trade|buy|sell|use|access|withdraw|move)\b", _F),
    re.compile(r"\b(withdraw|send|transfer|move)\b.{0,40}\b0x[a-f0-9]{40}\b", _F),
    re.compile(r"\b(dump|liquidate|cash\s+out|offload|unload|sell\s+off)\s+(all\s+(of\s+)?|half\s+(of\s+)?|"
               r"everything\s+in\s+)?my\b", _F),
    re.compile(r"\b(get|put|grab|buy|scoop|snag|pick)\s+(up\s+)?me\s+(into\b|in\s+on\b|some\b|a\s+bag\b|\$?\d)", _F),
    re.compile(r"\bape\s+\$?\d|\b(buy|sell|pick\s+up|grab|purchase|swap|trade|accumulate|acquire|scoop\s+up|snag)\b"
               r".{0,30}\bfor\s+me\b|\bget\s+(me\s+)?\$?\d[\d,.]*(?!\s*(d|h|m|w|days?|hours?|weeks?|months?|years?|"
               r"mins?|minutes?)\b)\s*[A-Za-z]{2,}\b.{0,15}\bfor\s+me\b", _F),
]

# Checked against a clause after its lead-in ("Hey,", "Also,", "Quick question:") is stripped.
CLAUSE_START_TRADE = [
    re.compile(r"^(dump|liquidate|cash\s+out|offload|unload|exchange|rebalance|close|move|put|ape|grab|scoop\s+up|snag|"
               r"deploy|allocate|invest|throw)\s+(me\s+)?(all\b|half\b|some\b|everything\b|\$?\d|my\b)", _F),
    re.compile(r"^(pick\s+up|scoop\s+up|load\s+up\s+on|ape\s+(into|in))\b|^(grab|snag)\s+(me\s+)?(some\s+|a\s+bag\s+of\s+|"
               r"\$?\d)", _F),
]
CLAUSE_START_ADVICE = [
    re.compile(r"^(do|shall|must)\s+(i|we)\s+(still\s+)?(buy|sell|hold|pick\s+up|grab|get|ape|dump|exit|enter|keep|add|"
               r"take\s+profits?|go\s+(long|short)|invest|load|accumulate|wait|offload|unload|scoop|cash\s+out|short|"
               r"long|stake|own)\b", _F),
    re.compile(r"^(hold|buy|sell|ape|stay|long|short)\s+or\s+(fold|sell|buy|hold|wait|dump|exit|run|long|short)\b", _F),
    re.compile(r"^(bullish|bearish)\s+(on|about)\b", _F),
    re.compile(r"^(good\s+|right\s+|best\s+)?time\s+to\s+(buy|sell|take\s+profits?|get\s+(in|out)|exit|enter|ape|load)\b",
               _F),
]

# Other languages, matched on lowercased text with accents stripped (so "debería" and "deberia" both hit).
FOREIGN_PATTERNS: list[tuple[Category, re.Pattern[str]]] = [
    # Spanish
    ("injection", re.compile(r"\b(ignora|olvida|omite|descarta)\b.{0,40}\b(instrucciones|reglas|normas|indicaciones)\b")),
    ("injection", re.compile(r"\b(actua|finge|haz)\s+(como|de|que\s+eres)\b|\b(prompt|mensaje|instrucciones)\s+del?\s+"
                             r"sistema\b|\b(tus|sus)\s+(instrucciones|reglas|directrices|indicaciones)\b")),
    ("trade", re.compile(r"\b(compra|comprame|vende|vendeme|cambia|transfiere|retira|envia)\s+(\d|todo|toda|todos|mi|mis)"
                         r"\b|\b(compr|vend)\w*\b.{0,30}\bpor\s+mi\b")),
    ("advice", re.compile(r"\b(deberia|debo|deberiamos|conviene|vale\s+la\s+pena)\s+(yo\s+)?(comprar|vender|invertir|"
                          r"mantener|holdear|entrar|salir|meter)\b|\b(es|sera|seria)\s+(un\s+|una\s+)?(buen|buena|mal|"
                          r"mala)\s+(momento|idea|inversion|oportunidad)\b|\bva\s+a\s+(subir|bajar|caer|explotar)\b|"
                          r"\b(vendo|compro)\s+(mi|mis|el|la|\w+)\b.{0,30}\b(o\s+espero|ahora|ya)\b")),
    # Portuguese
    ("injection", re.compile(r"\b(ignore|ignora|esqueca|desconsidere)\b.{0,40}\b(instrucoes|regras|diretrizes)\b|"
                             r"\b(aja|atue|finja)\s+(como|que)\b|\bprompt\s+(de|do)\s+sistema\b|\b(suas|tuas)\s+"
                             r"(instrucoes|regras|diretrizes)\b")),
    ("trade", re.compile(r"\b(compre|venda|troque|transfira|saque|envie)\s+(\d|todo|toda|tudo|meu|minha|meus|minhas|o|a)\b"
                         r"|\b(compr|vend)\w*\b.{0,30}\bpara\s+mim\b")),
    ("advice", re.compile(r"\b(devo|deveria|devemos)\s+(eu\s+)?(comprar|vender|investir|manter|segurar|entrar|sair)\b|"
                          r"\bvale\s+a\s+pena\s+(comprar|investir|vender|entrar|segurar)\b|\bvai\s+(subir|cair|descer|"
                          r"explodir|valorizar)\b|\b(bom|boa)\s+(momento|hora|investimento|ideia)\s+(para|pra)\s+"
                          r"(comprar|vender|investir)\b")),
    # Turkish
    ("injection", re.compile(r"\b(talimat|kural|yonerge)\w*\b.{0,40}\b(yok\s+say|unut|gormezden\s+gel|cevir|goster)\w*|"
                             r"\bsistem\s+istem\w*|\bgibi\s+davran\w*|\b(onceki|tum)\s+talimat\w*")),
    ("trade", re.compile(r"\bbenim\s+icin\b.{0,30}\b(al|sat|satin\s+al)\b|\b(varligimi|coinlerimi|bakiyemi|hesabimi)\b")),
    ("advice", re.compile(r"\b(almali|satmali|tutmali|yatirim\s+yapmali)\s*(mi|miyim|miyiz|mi\s+yim)\b|\b(yukselecek|"
                          r"dusecek|artacak|patlayacak|cokecek)\s+mi\b|\b(iyi|kotu|mantikli)\s+bir\s+yatirim\b")),
    # Vietnamese
    ("injection", re.compile(r"\bbo\s+qua\b.{0,40}\b(huong\s+dan|quy\s+tac|chi\s+dan)\b|\bquen\b.{0,30}\b(quy\s+tac|"
                             r"huong\s+dan)\b|\bdong\s+vai\b|\b(loi\s+nhac|huong\s+dan)\s+(he\s+thong|cua\s+ban)\b")),
    ("trade", re.compile(r"\b(mua|ban)\s+(giup|ho|cho)\s+toi\b|\bban\s+het\b")),
    ("advice", re.compile(r"\b(co\s+)?nen\s+(mua|ban|giu|dau\s+tu|chot)\b|\bco\s+(tang|giam)\b.{0,15}\bkhong\b|"
                          r"\bkhoan\s+dau\s+tu\s+(tot|xau)\b")),
    # Hindi, romanised
    ("injection", re.compile(r"\b(nirdesh\w*|niyam)\b.{0,30}\b(bhool|ignore|anadekha)\w*")),
    ("trade", re.compile(r"\bmere\s+liye\b|\b(buy|sell|kharid|bech)\w*\s+(kar\s+)?(do|dijiye|dena)\b")),
    ("advice", re.compile(r"\b(kharid\w*|bech\w*|le\s+lu|lena|lu)\b.{0,20}\b(chahiye|chaiye|sahi|theek|thik|accha|achha)"
                          r"\b")),
]
# Hindi in Devanagari, matched on the raw text (vowel signs are not \w, so no \b here).
DEVANAGARI_PATTERNS: list[tuple[Category, re.Pattern[str]]] = [
    ("injection", re.compile(r"(निर्देश|नियम)\S*\s*(\S+\s+){0,4}(अनदेखा|भूल|अनुवाद)|सिस्टम\s+प्रॉम्प्ट|"
                             r"(की\s+तरह|बनकर)\s+(जवाब|बात|व्यवहार)")),
    ("trade", re.compile(r"मेरे\s+लिए.{0,40}(खरीद|बेच)")),
    ("advice", re.compile(r"(खरीद|बेच|निवेश)\S*\s*(\S+\s+){0,3}चाहिए|(ऊपर|नीचे)\s+(जाएगा|जायेगा|जाएगी)")),
]

_SPLIT_CLAUSE = re.compile(r"(?<=[.!?;:])\s+|[\n\r]+|,\s*(?=(?:and\s+)?(?:then|after\s+that|also|anyway|but)\b)", _F)
_LEAD_IN = re.compile(
    r"^(?:(?:hey|hi|hello|yo|ok(?:ay)?|so|also|and|but|then|now|anyway|btw|p\.?s\.?|please|pls|plz|first|"
    r"quick\s+question|real\s+talk|serious\s+question|separate\s+question|one\s+more\s+thing|after\s+that|"
    r"i\s+was\s+wondering|i'?m\s+wondering|just\s+wondering|asking\s+for\s+a\s+friend|honestly)\b[\s,:;.!?-]*)+", _F)


def _clauses(text: str) -> list[str]:
    return [c for c in (_LEAD_IN.sub("", p.strip()) for p in _SPLIT_CLAUSE.split(text)) if c]


# ------------------------------------------------------------------ deobfuscation

_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d",
    "ԛ": "q", "ԝ": "w", "һ": "h", "ӏ": "l", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ѕ": "S", "Ј": "J", "ο": "o", "α": "a", "ν": "v",
    "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
_CONFUSABLE_CHARS = set("аеорсухіѕјԁԛԝһӏАВЕКМНОРСТХУІЅЈοανικρτυΑΒΕΖΗΙΚΜΝΟΡΤΥΧ")
_LEET = str.maketrans("013457@$8", "oieastasb")
_UNITISH = re.compile(r"^[(\[]?\$?\d[\d,.]*(k|m|b|h|d|w|x|s|bps|usd|usdt|usdc|%)?[)\]?.!,:;]*$|^0x[0-9a-f]+[.,!?]*$", _F)
_B64_TOKEN = re.compile(r"(?<![\w+/=-])[A-Za-z0-9+/_-]{16,}={0,2}(?![\w+/=-])")
_CONTRACTIONS = {"youre": "you're", "whats": "what's", "wheres": "where's", "hows": "how's", "dont": "don't",
                 "doesnt": "doesn't", "isnt": "isn't", "wont": "won't", "cant": "can't", "im": "i'm", "ive": "i've",
                 "shouldnt": "shouldn't", "wouldnt": "wouldn't", "didnt": "didn't", "arent": "aren't"}
_DECODE_REQUEST = re.compile(r"\b(base\s?64|rot[\s-]?13)\b|\b(decode|decipher|decrypt)\b.{0,60}\b(answer|reply|respond|"
                             r"do\s+what|follow|obey)\b", _F)
_ROT13_HINT = re.compile(r"\b(rot[\s-]?13|caesar|cipher)\b", _F)
_REVERSE_HINT = re.compile(r"\b(revers\w*|backwards?|mirrored)\b", _F)


def _join_spaced_letters(raw: str) -> str:
    """'p r o m p t' -> 'prompt'. Double spaces, when present, mark the real word breaks."""
    chunks = re.split(r"[ \t]{2,}", raw)
    if len(chunks) > 1:
        return " ".join(c.replace(" ", "") if re.fullmatch(r"(\S ){1,}\S", c) else c for c in chunks)
    return re.sub(r"\b(?:\w ){2,}\w\b", lambda m: m.group(0).replace(" ", ""), raw)


def _deleet(text: str) -> str:
    tokens = text.split(" ")
    mixed = [w for w in tokens if re.search(r"[A-Za-z]", w) and re.search(r"[0-9@$]", w) and not _UNITISH.match(w)]
    if not mixed:
        return text
    heavy = len(mixed) >= 2  # "15 17 W153 ..." : bare digits are letters too

    def fix(w: str) -> str:
        if _UNITISH.match(w) and not (heavy and re.fullmatch(r"\d{1,3}", w)):
            return w
        return w.translate(_LEET) if (re.search(r"[A-Za-z]", w) or heavy) else w

    return " ".join(fix(w) for w in tokens)


def _rejoin_and_fix_typos(text: str) -> str:
    """Rejoin words split by a space ('in structions') and undo one swapped-letter typo ('Wuold')."""
    tokens = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(tokens):
        a = tokens[i]
        if i + 1 < len(tokens) and a.isalpha():
            m = re.match(r"([A-Za-z]+)(.*)$", tokens[i + 1])
            if m and (a + m.group(1)).lower() in _VOCAB and a.lower() not in _VOCAB and m.group(1).lower() not in _VOCAB:
                out.append(a + m.group(1) + m.group(2))
                i += 2
                continue
        out.append(a)
        i += 1
    return " ".join(_untypo(w) for w in out)


def _untypo(word: str) -> str:
    m = re.match(r"^([^A-Za-z]*)([A-Za-z]{5,})([^A-Za-z]*)$", word)
    if not m or m.group(2).lower() in _VOCAB:
        return word
    core = m.group(2).lower()
    for j in range(len(core) - 1):
        swapped = core[:j] + core[j + 1] + core[j] + core[j + 2:]
        if swapped in _VOCAB:
            return m.group(1) + swapped + m.group(3)
    return word


def _deobfuscate(raw: str) -> str:
    t = unicodedata.normalize("NFKC", raw)
    t = "".join(c for c in t if unicodedata.category(c) != "Cf" and not 0xFE00 <= ord(c) <= 0xFE0F)
    if re.search(r"[A-Za-z]", t) and any(c in _CONFUSABLE_CHARS for c in t):
        t = t.translate(_CONFUSABLES)
    t = _join_spaced_letters(t)
    t = re.sub(r"(?<=[A-Za-z])[*_.\-·•|/](?=[A-Za-z])", "", t)  # sh*ould, b.u.y
    t = " ".join(t.split())
    t = re.sub(r"\s[,;]\s", " ", t)  # stray commas dropped between words
    t = re.sub(r"\b[A-Za-z]{2,8}\b", lambda m: _CONTRACTIONS.get(m.group(0).lower(), m.group(0)), t)  # "Youre" -> "you're"
    return _rejoin_and_fix_typos(_deleet(t))


def _b64_text(tok: str) -> str | None:
    """The token decoded as base64, if that gives readable text (also when its letter case was scrambled)."""
    tok = tok + "=" * (-len(tok) % 4)
    alt = b"-_" if re.search(r"[-_]", tok) else None
    try:
        s = base64.b64decode(tok, altchars=alt).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        s = None
    if s is None or not _readable(s):
        s = _b64_recase(tok, alt)
    return s if s is not None and _readable(s) else None


def _readable(s: str) -> bool:
    printable = sum(c.isprintable() for c in s) / max(len(s), 1)
    return printable >= 0.95 and len(re.findall(r"[A-Za-z]", s)) >= 4 and " " in s


def _b64_recase(tok: str, alt: bytes | None) -> str | None:
    """Undo case noise on base64: each 4-char block decodes on its own, so pick its most text-like casing."""
    out = bytearray()
    for i in range(0, len(tok), 4):
        block = tok[i:i + 4]
        best, best_score = None, -1
        letters = [j for j, c in enumerate(block) if c.isalpha()]
        for mask in range(1 << len(letters)):
            chars = list(block)
            for k, j in enumerate(letters):
                if mask >> k & 1:
                    chars[j] = chars[j].swapcase()
            cand = "".join(chars)
            try:
                raw = base64.b64decode(cand, altchars=alt)
            except (binascii.Error, ValueError):
                continue
            if not all(32 <= b < 127 for b in raw):
                continue
            score = sum(2 if chr(b).islower() or b == 32 else 1 if chr(b).isalpha() else 0 for b in raw)
            if score > best_score:
                best, best_score = raw, score
        if best is None:
            return None
        out += best
    return out.decode("ascii")


def _decode_base64(text: str) -> str | None:
    found = False

    def repl(m: re.Match[str]) -> str:
        nonlocal found
        s = _b64_text(m.group(0))
        found = found or s is not None
        return s if s is not None else m.group(0)

    out = _B64_TOKEN.sub(repl, text)
    return out if found else None


def _deaccent(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I").replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _variants(question: str) -> list[str]:
    """The text as written, then a de-obfuscated copy, then anything it decodes to."""
    base = " ".join(question.split())
    clean = _deobfuscate(question)
    decoded = [d for d in (_decode_base64(base), _decode_base64(clean)) if d]
    if _ROT13_HINT.search(base) or _ROT13_HINT.search(clean):
        decoded.append(codecs.decode(base, "rot13"))
    if _REVERSE_HINT.search(base) or _REVERSE_HINT.search(clean):
        decoded.append(base[::-1])
    out = [base]
    for v in [clean, *(_deobfuscate(d) for d in decoded)]:
        if v and v not in out:
            out.append(v)
    return out


def _words_in(patterns: list[re.Pattern[str]]) -> set[str]:
    words: set[str] = set()
    for p in patterns:
        src = re.sub(r"\\[A-Za-z]", " ", p.pattern).lower()
        for pick in (r"\1", r"\2"):  # "summari[sz]e" -> summarise, summarize
            words |= set(re.findall(r"[a-z]{4,}", re.sub(r"\[([a-z])([a-z])\]", pick, src)))
    return words


# Words the patterns look for; split words and swapped-letter typos are repaired only towards these.
_VOCAB = _words_in(
    INJECTION_PATTERNS + EXTRA_INJECTION_PATTERNS + ADVICE_PATTERNS + EXTRA_ADVICE_PATTERNS + TRADE_PATTERNS
    + ACCOUNT_PATTERNS + EXTRA_TRADE_PATTERNS + CLAUSE_START_ADVICE + CLAUSE_START_TRADE
    + [p for _, p in FOREIGN_PATTERNS]
) | {"your", "give", "tell", "which", "coin", "should", "system", "prompt", "signal", "ignore", "previous",
     "instructions", "translate", "spell", "print", "would", "given", "output", "describe"}


REFUSAL_MESSAGES: dict[Category, str] = {
    "advice": (
        "I can't tell you whether to buy, sell or hold, and I don't make price predictions. I'm a market "
        "research tool, not a licensed financial advisor, and short-term prices can't be predicted reliably."
    ),
    "trade": (
        "I can't place trades or access any account. TickerSage is read-only by design: it has no order, "
        "wallet or account tools at all, so nothing here could execute a trade even if asked."
    ),
    "injection": (
        "I can't follow instructions that try to change how I work. I answer questions about crypto markets "
        "using public exchange data, and my rules stay the same."
    ),
    "off_topic": (
        "I only cover crypto markets: prices, volume, volatility, order books, news sentiment and crypto "
        "concepts."
    ),
}

FOLLOW_UPS: dict[Category, str] = {
    "advice": "I can show you the price, 24h move, volatility and recent news sentiment for any listed asset.",
    "trade": "If it helps, I can show you current market data for the asset instead.",
    "injection": "Try asking something like \"What's BTC trading at?\" or \"What is a funding rate?\"",
    "off_topic": "Try asking \"Compare ETH and SOL over the last 7 days\" or \"What is a funding rate?\"",
}


@dataclass
class GuardResult:
    allowed: bool
    category: Category | None = None
    reason: str | None = None
    matched: list[str] = field(default_factory=list)


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def check_input(question: str) -> GuardResult:
    """Check the question as written, then each de-obfuscated or decoded variant of it."""
    for i, text in enumerate(_variants(question)):
        g = _check_text(text)
        if not g.allowed:
            if i:
                g.reason = f"{g.reason} (after decoding obfuscated text)"
            return g
    if _DECODE_REQUEST.search(question) and any(_b64_text(t) is None for t in _B64_TOKEN.findall(question)):
        return GuardResult(False, "injection", "asked to decode a payload that doesn't decode to readable text", [])
    return GuardResult(True)


# Per clause, a key that is only mentioned ("apiKey=[REDACTED]") is not a request to act; the whole-message check
# above still refuses "here's my API key, connect to my account".
_CLAUSE_TRADE_PATTERNS = TRADE_PATTERNS + [p for p in ACCOUNT_PATTERNS if "api[- ]?key" not in p.pattern] \
    + EXTRA_TRADE_PATTERNS + CLAUSE_START_TRADE


def _foreign(text: str, category: Category) -> str | None:
    plain = _rejoin_and_fix_typos(_deaccent(text))
    for cat, p in FOREIGN_PATTERNS:
        if cat == category and (m := p.search(plain)):
            return m.group(0)
    for cat, p in DEVANAGARI_PATTERNS:
        if cat == category and (m := p.search(text)):
            return m.group(0)
    return None


def _check_text(text: str) -> GuardResult:
    hit = _first_match(INJECTION_PATTERNS, text) or _first_match(EXTRA_INJECTION_PATTERNS, text) \
        or _foreign(text, "injection")
    if hit:
        return GuardResult(False, "injection", f"prompt injection pattern: {hit!r}", [hit])

    concept = bool(_CONCEPT_QUESTION.search(text)) and not _ACT_FOR_ME.search(text)
    if not concept:
        hit = _first_match(TRADE_PATTERNS, text) or _first_match(ACCOUNT_PATTERNS, text)
        if hit:
            return GuardResult(False, "trade", f"trade/account request: {hit!r}", [hit])
    elif _ACT_FOR_ME.search(text):
        return GuardResult(False, "trade", "asked to act on the user's behalf", [])

    clauses = _clauses(text)
    for clause in clauses:  # a concept opener only exempts its own clause
        if _CONCEPT_QUESTION.search(clause) and not _ACT_FOR_ME.search(clause):
            continue
        hit = _first_match(_CLAUSE_TRADE_PATTERNS, clause)
        if hit:
            return GuardResult(False, "trade", f"trade/account request: {hit!r}", [hit])
    hit = _foreign(text, "trade")
    if hit:
        return GuardResult(False, "trade", f"trade/account request: {hit!r}", [hit])

    hit = _first_match(ADVICE_PATTERNS, text)
    if hit and not _is_concept_definition(text, hit):
        return GuardResult(False, "advice", f"advice/prediction request: {hit!r}", [hit])
    for part in [text, *clauses]:
        for patterns in (ADVICE_PATTERNS, EXTRA_ADVICE_PATTERNS, CLAUSE_START_ADVICE):
            hit = _first_match(patterns, part)
            if hit and not _is_concept_definition(part, hit):
                return GuardResult(False, "advice", f"advice/prediction request: {hit!r}", [hit])
    hit = _foreign(text, "advice")
    if hit:
        return GuardResult(False, "advice", f"advice/prediction request: {hit!r}", [hit])
    return GuardResult(True)


def _is_concept_definition(text: str, hit: str) -> bool:
    """'What is a price target?' asks for a definition, not a prediction."""
    definition = re.match(
        r"^\s*(what|what's|whats)\s+(is|are|does)\s+(a|an|the)?\s*" + re.escape(hit.split()[0]), text, _F
    ) or re.match(r"^\s*(explain|define|describe)\b", text, _F)
    asks_for_call = re.search(r"\b(should\s+(i|we)|will\s+\w+\s+(go|rise|fall|reach|hit))\b", text, _F)
    return bool(definition) and not asks_for_call


def detect_injection(text: str) -> str | None:
    """Return the matching span when untrusted tool text contains instructions (obfuscated or not)."""
    for variant in _variants(text):
        hit = _first_match(TOOL_TEXT_INJECTION_PATTERNS, variant)
        if hit:
            return hit
    return None


# ------------------------------------------------------------------ output

ADVICE_OUTPUT_PATTERNS = [
    re.compile(r"\byou\s+(should|could|might\s+want\s+to|may\s+want\s+to)\s+(consider\s+)?(buy|sell|hold|invest|short|long|accumulate|take\s+profit)\w*", _F),
    re.compile(r"\b(I|we)\s+(would\s+)?(recommend|suggest|advise)\s+(buying|selling|holding|investing|shorting|that\s+you)", _F),
    re.compile(r"\b(good|great|perfect|ideal|excellent)\s+(time|opportunity|entry|moment)\s+to\s+(buy|sell|enter|exit)\b", _F),
    re.compile(r"\b(strong\s+)?(buy|sell)\s+signal\b", _F),
    re.compile(r"\b(will|is\s+going\s+to|is\s+likely\s+to|is\s+expected\s+to|should)\s+(likely\s+)?(rise|fall|go\s+up|go\s+down|reach|hit|rally|crash|pump|dump|recover|bounce|moon)\b", _F),
    re.compile(r"\bguarantee", _F),
    re.compile(r"\bprice\s+target\b", _F),
    re.compile(r"\bbuy\s+the\s+dip\b", _F),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_\-\[(])")


@dataclass
class OutputCheck:
    text: str
    removed: list[str] = field(default_factory=list)


def check_output(answer: str) -> OutputCheck:
    """Drop sentences that read like advice or predictions."""
    kept_lines: list[str] = []
    removed: list[str] = []
    for line in answer.split("\n"):
        parts = _SENTENCE_SPLIT.split(line)
        kept = []
        for sentence in parts:
            if _first_match(ADVICE_OUTPUT_PATTERNS, sentence):
                removed.append(sentence.strip())
            else:
                kept.append(sentence)
        kept_lines.append(" ".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    if removed:
        log.info("output guard removed %d sentence(s)", len(removed))
    return OutputCheck(text=text, removed=removed)


def data_footer(as_of: datetime | None, sources: list[str]) -> str:
    if as_of is None:
        return "_Market research only. This is not investment advice._"
    src = ", ".join(dict.fromkeys(sources)) or "public exchange API"
    return f"_Data as of {as_of:%H:%M} UTC on {as_of:%Y-%m-%d} from {src}. Market data only, not investment advice._"


def log_refusal(request_id: str, category: str, reason: str | None) -> None:
    log.info("refusal request_id=%s category=%s reason=%s", request_id, category, reason)
