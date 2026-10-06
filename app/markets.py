"""Markets and the prefixes used to mine App Store search suggestions.

A market is a storefront plus the alphabet people type in. Suggestions depend
on the storefront and the typed text only (not on the UI language), so e.g.
India (Tamil) is the IN storefront probed with Tamil letters. Markets whose
storefront and alphabets coincide share one scan (Catalonia reuses Spain).
"""

from __future__ import annotations

LATIN = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"

ALPHABETS = {
    "latin": LATIN,
    "cyrillic_ru": "абвгдежзийклмнопрстуфхцчшщэюя",
    "cyrillic_uk": "абвгґдеєжзиіїйклмнопрстуфхцчшщюя",
    "greek": "αβγδεζηθικλμνξοπρστυφχψω",
    "hebrew": "אבגדהוזחטיכלמנסעפצקרשת",
    "arabic": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
    "urdu": "ابپتٹثجچحخدڈذرڑزژسشصضطظعغفقکگلمنوہیے",
    "thai": "กขคฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ",
    "devanagari": "अआइईउएओकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह",
    "bengali": "অআইঈউএওকখগঘচছজঝটঠডঢতথদধনপফবভমযরলশষসহ",
    "gujarati": "અઆઇઈઉએઓકખગઘચછજઝટઠડઢતથદધનપફબભમયરલવશષસહ",
    "gurmukhi": "ਅਆਇਈਉਏਓਕਖਗਘਚਛਜਝਟਠਡਢਤਥਦਧਨਪਫਬਭਮਯਰਲਵਸਹ",
    "oriya": "ଅଆଇଈଉଏଓକଖଗଘଚଛଜଝଟଠଡଢତଥଦଧନପଫବଭମଯରଲଵଶଷସହ",
    "tamil": "அஆஇஈஉஊஎஏஐஒஓகஙசஞடணதநபமயரலவழளறன",
    "telugu": "అఆఇఈఉఎఏఒఓకఖగఘచఛజఝటఠడఢతథదధనపఫబభమయరలవశషసహ",
    "kannada": "ಅಆಇಈಉಎಏಒಓಕಖಗಘಚಛಜಝಟಠಡಢತಥದಧನಪಫಬಭಮಯರಲವಶಷಸಹ",
    "malayalam": "അആഇഈഉഎഏഒഓകഖഗഘചഛജഝടഠഡഢതഥദധനപഫബഭമയരലവശഷസഹ",
    "hiragana": "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ",
    # Korean is typed in syllables: the most common leading ones.
    "hangul": "가나다라마바사아자차카타파하고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후"
              "게네데레메배세에제체케테페해기니디리미비시이지치키티피히",
    # Chinese: frequent leading characters (pinyin input is covered by Latin).
    "hanzi": "微王百中美抖快淘支腾网京小天大手高学爱拼饿滴携拍有新和云华招工建农交平国好视音游地全",
    "hanzi_trad": "台中國新蝦全街愛好手天大小高學網電遊音樂地銀行臺麥蘋時聯統富國泰玉山華國",
}

# Suggestions are probed with every letter (depth 1); depth 2 also probes every
# two-letter combination. Scripts with big alphabets stay at depth 1.
Seed = tuple  # (alphabet name, depth)

LATIN_DEEP: tuple[Seed, ...] = (("latin", 2),)


def native(script: str, depth: int = 1, latin: bool = True) -> tuple[Seed, ...]:
    """A native alphabet, plus Latin letters for brand names typed in English."""
    return ((script, depth),) + ((("latin", 1),) if latin else ())


# (locale, name, storefront country, App Store storefront id, seeds)
_MARKETS = [
    ("he", "Israel", "il", 143491, native("hebrew")),
    ("tr", "Turkey", "tr", 143480, LATIN_DEEP),
    ("cs", "Czech Republic", "cz", 143489, LATIN_DEEP),
    ("it", "Italy", "it", 143450, LATIN_DEEP),
    ("zh-Hant", "Taiwan", "tw", 143470, native("hanzi_trad")),
    ("zh-Hans", "China", "cn", 143465, native("hanzi")),
    ("da", "Denmark", "dk", 143458, LATIN_DEEP),
    ("es-ES", "Spain", "es", 143454, LATIN_DEEP),
    ("mr-IN", "India (Marathi)", "in", 143467, native("devanagari", latin=False)),
    ("nl-NL", "Netherlands", "nl", 143452, LATIN_DEEP),
    ("or-IN", "India (Odia)", "in", 143467, native("oriya", latin=False)),
    ("kn-IN", "India (Kannada)", "in", 143467, native("kannada", latin=False)),
    ("gu-IN", "India (Gujarati)", "in", 143467, native("gujarati", latin=False)),
    ("en-GB", "United Kingdom", "gb", 143444, LATIN_DEEP),
    ("pt-PT", "Portugal", "pt", 143453, LATIN_DEEP),
    ("ro", "Romania", "ro", 143487, LATIN_DEEP),
    ("el", "Greece", "gr", 143448, native("greek")),
    ("en-IN", "India", "in", 143467, LATIN_DEEP),
    ("uk", "Ukraine", "ua", 143492, native("cyrillic_uk", depth=2)),
    ("ru", "Russia", "ru", 143469, native("cyrillic_ru", depth=2)),
    ("bn-BD", "Bangladesh", "bd", 143490, native("bengali")),
    ("sk", "Slovakia", "sk", 143496, LATIN_DEEP),
    ("pt-BR", "Brazil", "br", 143503, LATIN_DEEP),
    ("th", "Thailand", "th", 143475, native("thai")),
    ("ar-SA", "Saudi Arabia", "sa", 143479, native("arabic")),
    ("de-DE", "Germany", "de", 143443, LATIN_DEEP),
    ("id", "Indonesia", "id", 143476, LATIN_DEEP),
    ("en-AU", "Australia", "au", 143460, LATIN_DEEP),
    ("fi", "Finland", "fi", 143447, LATIN_DEEP),
    ("no", "Norway", "no", 143457, LATIN_DEEP),
    ("vi", "Vietnam", "vn", 143471, LATIN_DEEP),
    ("en-CA", "Canada", "ca", 143455, LATIN_DEEP),
    ("hr", "Croatia", "hr", 143494, LATIN_DEEP),
    ("ko", "South Korea", "kr", 143466, native("hangul")),
    ("ja", "Japan", "jp", 143462, native("hiragana")),
    ("ml-IN", "India (Malayalam)", "in", 143467, native("malayalam", latin=False)),
    ("te-IN", "India (Telugu)", "in", 143467, native("telugu", latin=False)),
    ("en-US", "United States", "us", 143441, LATIN_DEEP),
    ("ur-PK", "Pakistan", "pk", 143477, native("urdu")),
    ("ca", "Catalonia", "es", 143454, LATIN_DEEP),
    ("ms", "Malaysia", "my", 143473, LATIN_DEEP),
    ("ta-IN", "India (Tamil)", "in", 143467, native("tamil", latin=False)),
    ("sv", "Sweden", "se", 143456, LATIN_DEEP),
    ("fr-FR", "France", "fr", 143442, LATIN_DEEP),
    ("pa-IN", "India (Punjabi)", "in", 143467, native("gurmukhi", latin=False)),
    ("hi", "India (Hindi)", "in", 143467, native("devanagari", latin=False)),
    ("pl", "Poland", "pl", 143478, LATIN_DEEP),
    ("es-MX", "Mexico", "mx", 143468, LATIN_DEEP),
    ("sl-SI", "Slovenia", "si", 143499, LATIN_DEEP),
    ("hu", "Hungary", "hu", 143482, LATIN_DEEP),
]


def _scan_key(country: str, seeds: tuple[Seed, ...]) -> str:
    return country + ":" + "+".join(f"{a}{d}" for a, d in seeds)


MARKETS: dict[str, dict] = {}
for _loc, _name, _cc, _sf, _seeds in _MARKETS:
    MARKETS[_loc] = {
        "locale": _loc, "name": _name, "country": _cc, "storefront": _sf,
        "seeds": [{"alphabet": a, "depth": d} for a, d in _seeds],
        "scan_key": _scan_key(_cc, _seeds),
    }

# Markets that reuse another market's scan (same storefront and alphabets).
_first_by_key: dict[str, str] = {}
for _m in MARKETS.values():
    _m["shares_with"] = _first_by_key.setdefault(_m["scan_key"], _m["locale"])
    if _m["shares_with"] == _m["locale"]:
        _m["shares_with"] = None


def scan_units() -> list[dict]:
    """One market per distinct scan key: what the scanner actually probes."""
    return [m for m in MARKETS.values() if m["shares_with"] is None]

